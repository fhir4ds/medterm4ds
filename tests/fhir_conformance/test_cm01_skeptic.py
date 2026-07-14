"""SKEPTIC probes for chunk CM-01 (ConceptMap Resource Structure).

Source: https://build.fhir.org/conceptmap.html
Canonical R4 equivalence enum:
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html

Chunk scope (6 items):
  1. ``group.element.target.equivalence`` values from R4 enum (10 values).
  2. ``group.element.target.dependsOn`` for parameterized mappings.
  3. ``group.element.target.product`` for downstream concept derivations.
  4. ``group.source`` / ``group.target`` scoping fields.
  5. ``ConceptMap.url`` canonical identifier.
  6. READ and SEARCH interactions on ConceptMap.

SKEPTIC lens (adversarial bug hunting):
  * Equivalence vocabulary audit — every value emitted by $translate
    MUST be in the R4 closed enum. CF-HISTORIAN-VS01-01 RESOLVED-status
    verification (R5 ``subsumedby`` → R4 ``specializes``).
  * CR-012 (HIGH, milestone-2 review): ``_do_translate`` echoes client
    ``source_uri`` verbatim in Out ``match[].source.system``. Verify
    the structural fix (``canonical_system_uri`` helper).
  * NEW (this iteration): ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` has the
    R4 directionality of ``narrower`` / ``wider`` INVERTED relative to
    the engine vocabulary ``source-is-narrower-than-target`` /
    ``source-is-broader-than-target``. Per R4 spec the equivalence is
    read from TARGET perspective: ``wider`` means "target is wider than
    source". The same engine relationship resolves to OPPOSITE R4 values
    depending on whether the response goes through ``responses.py``
    ($translate) or ``outputs/fhir.py`` (ConceptMap export).
"""

from __future__ import annotations

import pytest

from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
    canonical_system_uri,
)


# ---------------------------------------------------------------------------
# Lens 1: Equivalence vocabulary closed-enum audit (CF-HISTORIAN-VS01-01
# RESOLVED-status verification).
# ---------------------------------------------------------------------------


def test_s10_internal_rel_to_fhir_equivalence_emits_only_r4_values():
    """SKEPTIC: every value emitted by ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE``
    MUST be a member of the FHIR R4 ConceptMapEquivalence closed enum.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    CF-HISTORIAN-VS01-01 (milestone-2 review) fixed two prior drift values
    (``subsumedby`` R5/R4B and ``not-relatedto`` not-in-any-enum). The
    production-side ``assert`` in ``responses.py`` enforces this at module
    load time, but a future change to the map could break the invariant
    without updating the constant. This probe is the registry-as-contract
    safety net.
    """
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE

    emitted = set(_INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
    drift = emitted - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert not drift, (
        f"_INTERNAL_REL_TO_FHIR_EQUIVALENCE emits values outside the FHIR R4 "
        f"ConceptMapEquivalence closed enum: {drift}. Regression of "
        f"CF-HISTORIAN-VS01-01."
    )


def test_s11_cf_historian_vs01_01_subsumedby_resolved_to_specializes():
    """SKEPTIC: CF-HISTORIAN-VS01-01 RESOLVED-status verification.

    The prior map emitted ``subsumedby`` (R5/R4B value) for both engine
    relationship keys ``subsumedby`` and ``subsumed-by``. Per R4 spec
    the spec-correct value is ``specializes``. The milestone-2
    remediation (FIX-003) replaced both. This probe is the carry-forward
    pin: if the regression returns, the probe fails loudly.
    """
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE

    assert _INTERNAL_REL_TO_FHIR_EQUIVALENCE["subsumedby"] == "specializes", (
        "CF-HISTORIAN-VS01-01 regression: subsumedby key must map to "
        "R4 spec-correct value 'specializes' (not R5/R4B 'subsumedby')."
    )
    assert _INTERNAL_REL_TO_FHIR_EQUIVALENCE["subsumed-by"] == "specializes", (
        "CF-HISTORIAN-VS01-01 regression: subsumed-by key must map to "
        "R4 spec-correct value 'specializes'."
    )


def test_s12_cf_historian_vs01_01_not_relatedto_resolved_to_unmatched():
    """SKEPTIC: CF-HISTORIAN-VS01-01 RESOLVED-status verification (part 2).

    The prior map emitted ``not-relatedto`` (not in ANY FHIR enum) for the
    engine relationship key ``not-relatedto``. Per R4 spec the catch-all
    for "no mapping" is ``unmatched``. The milestone-2 remediation
    (FIX-003) replaced it. This probe pins the resolved state.
    """
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE

    assert _INTERNAL_REL_TO_FHIR_EQUIVALENCE["not-relatedto"] == "unmatched", (
        "CF-HISTORIAN-VS01-01 regression: not-relatedto key must map to "
        "R4 spec-correct catch-all 'unmatched' (not 'not-relatedto' which "
        "is not in any FHIR enum)."
    )


def test_s13_assertion_at_module_load_guards_drift():
    """SKEPTIC: the canonical equivalence module
    (``engines/fhir/equivalence.py``) has a module-load ``assert`` that
    every value in ``INTERNAL_REL_TO_FHIR_EQUIVALENCE`` is in
    ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE``. If the assertion is removed,
    future drift would silently land on BOTH the $translate HTTP surface
    (``responses.py``) AND the ConceptMap export surface
    (``outputs/fhir.py``) — both import the canonical map.

    Pre-CR-024 (milestone-2 state): the assert lived in ``responses.py``
    and guarded only the $translate surface; ``outputs/fhir.py`` had no
    equivalent guard. Post-CR-024: the assert is in the canonical module
    and guards both surfaces uniformly.
    """
    import inspect

    from medterm4ds.engines.fhir import equivalence as equiv_module

    source = inspect.getsource(equiv_module)
    assert "FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in source, (
        "engines/fhir/equivalence.py MUST reference FHIR_R4_CONCEPT_MAP_EQUIVALENCE "
        "in a module-load assertion guarding closed-enum drift "
        "(CF-HISTORIAN-VS01-01, CR-024)."
    )
    assert "assert" in source and "INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()" in source, (
        "engines/fhir/equivalence.py MUST have a module-load assert enforcing "
        "INTERNAL_REL_TO_FHIR_EQUIVALENCE.values() <= FHIR_R4_CONCEPT_MAP_EQUIVALENCE."
    )


# ---------------------------------------------------------------------------
# Lens 2: R4 directionality of narrower / wider — CRITICAL NEW FINDING.
# ---------------------------------------------------------------------------


def test_s20_internal_rel_narrower_wider_directionality_per_r4_spec():
    """SKEPTIC (CRITICAL NEW FINDING): the R4 ``equivalence`` enum is read
    from the TARGET perspective relative to the source.

    Per https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html:
      * ``wider``    = "The target mapping is WIDER in meaning than the
                        source concept."
      * ``narrower`` = "The target mapping is NARROWER in meaning than the
                        source concept."

    The medterm4ds engine vocabulary uses the R5 naming convention that
    makes direction explicit: ``source-is-narrower-than-target`` and
    ``source-is-broader-than-target``.

    Translation table (per R4 spec):
      * ``source-is-narrower-than-target`` → R4 ``wider``
        (source narrower ⇒ target wider)
      * ``source-is-broader-than-target`` → R4 ``narrower``
        (source broader ⇒ target narrower)

    But ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` in responses.py has them
    INVERTED:
      * line 133: ``"source-is-narrower-than-target": "narrower"``  WRONG
      * line 135: ``"source-is-broader-than-target": "wider"``      WRONG

    Cross-check: ``outputs/fhir.py:FHIR_EQUIVALENCES`` has them CORRECT
    (already pinned by ``test_fhir_outputs.py:128-129``). The two maps
    disagree.
    """
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE

    # Per R4 spec the target is the subject of the equivalence code.
    # ``source-is-narrower-than-target`` means target is wider.
    assert _INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-narrower-than-target"] == "wider", (
        "R4 spec-directionality bug: 'source-is-narrower-than-target' "
        "means target is wider than source. R4 equivalence is read from "
        "the TARGET perspective, so the spec-correct value is 'wider'. "
        "responses.py currently emits 'narrower' (WRONG)."
    )
    assert _INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-broader-than-target"] == "narrower", (
        "R4 spec-directionality bug: 'source-is-broader-than-target' "
        "means target is narrower than source. R4 equivalence is read from "
        "the TARGET perspective, so the spec-correct value is 'narrower'. "
        "responses.py currently emits 'wider' (WRONG)."
    )


def test_s21_outputs_fhir_module_and_responses_module_agree_on_directionality():
    """SKEPTIC: the two maps ``outputs/fhir.py:FHIR_EQUIVALENCES`` and
    ``responses.py:_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` translate the SAME
    engine vocabulary but live in different modules. They MUST agree on
    the R4 value for every shared key. A future regression in either file
    would silently produce opposite R4 codes for the same input — the
    $translate and ConceptMap-export surfaces would disagree on clinical
    semantics.
    """
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE
    from medterm4ds.outputs.fhir import FHIR_EQUIVALENCES

    shared_keys = (
        set(_INTERNAL_REL_TO_FHIR_EQUIVALENCE.keys())
        & set(FHIR_EQUIVALENCES.keys())
    )
    assert shared_keys, (
        "No shared keys between _INTERNAL_REL_TO_FHIR_EQUIVALENCE and "
        "FHIR_EQUIVALENCES — they translate the same engine vocabulary "
        "and SHOULD share at least the core keys."
    )
    disagreements = []
    for key in sorted(shared_keys):
        a = _INTERNAL_REL_TO_FHIR_EQUIVALENCE[key]
        b = FHIR_EQUIVALENCES[key]
        if a != b:
            disagreements.append((key, a, b))
    assert not disagreements, (
        f"The two equivalence maps disagree on shared keys: {disagreements}. "
        f"They MUST agree — same engine relationship MUST produce the same "
        f"R4 equivalence code on every surface."
    )


def test_s22_outputs_fhir_directionality_pinned_correct():
    """SKEPTIC: ``outputs/fhir.py`` is the correct reference. This probe
    documents the spec-correct directionality and protects against
    regression.
    """
    from medterm4ds.outputs.fhir import fhir_equivalence

    assert fhir_equivalence("source-is-narrower-than-target") == "wider"
    assert fhir_equivalence("source-is-broader-than-target") == "narrower"


# ---------------------------------------------------------------------------
# Lens 3: CR-012 RESOLVED-status verification.
# ---------------------------------------------------------------------------


def test_s30_cr012_do_translate_uses_canonical_system_uri_helper():
    """SKEPTIC: CR-012 (HIGH, milestone-2 review) — ``_do_translate`` echoes
    client ``source_uri`` verbatim in Out ``match[].source.system``. The
    structural fix (milestone-2 FIX-002) wraps the source URI through the
    shared ``canonical_system_uri()`` helper. Verify the helper is wired
    into ``_do_translate`` by reading the source text.
    """
    import inspect

    from medterm4ds.apps.fhir_api import create_fhir_app
    from medterm4ds.apps.fhir_api import FhirApiSettings

    # create_fhir_app is a factory; inspect its source for _do_translate
    # and the canonical_system_uri helper call.
    src = inspect.getsource(create_fhir_app)
    assert "canonical_system_uri" in src, (
        "CR-012 regression: _do_translate must call canonical_system_uri "
        "on the client-supplied source_uri before passing to "
        "build_parameters_translate. Source has no such call."
    )
    assert "canonical_source_uri" in src or "canonical_uri" in src, (
        "CR-012 regression: _do_translate must capture the canonical URI "
        "into a local variable and pass it to build_parameters_translate."
    )


def test_s31_translate_match_source_system_is_canonical(fhir_client):
    """SKEPTIC: end-to-end probe for CR-012. Calling $translate with an
    alias URI (``urn:oid:2.16.840.1.113883.6.96`` is the SNOMED OID) MUST
    return a canonical URI (``http://snomed.info/sct``) in Out
    ``match[].source.system`` — not the alias verbatim.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "urn:oid:2.16.840.1.113883.6.96",
            "code": "44054006",
            "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm",
        },
    )
    body = r.json()
    if body.get("resourceType") == "OperationOutcome":
        pytest.skip("fixture DB missing the test code")
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    if not matches:
        pytest.skip("no matches for the test code")
    for m in matches:
        src_part = next(
            (part for part in m.get("part", []) if part.get("name") == "source"),
            None,
        )
        assert src_part is not None, "match.part missing 'source'"
        src_system = src_part.get("valueCoding", {}).get("system")
        assert src_system == "http://snomed.info/sct", (
            f"CR-012 regression: $translate called with urn:oid alias returned "
            f"match[].source.system={src_system!r}; expected canonical "
            f"'http://snomed.info/sct'. The alias was echoed verbatim."
        )


def test_s32_translate_target_system_canonical_trailing_slash(fhir_client):
    """SKEPTIC: trailing-slash alias should also resolve to canonical.
    The ``responses.py`` builder already canonicalizes the target side
    (via ``system_to_fhir_uri(m.target.source)`` at responses.py line
    ~205). This probe documents that path; CR-012 covered the source
    side, this covers the target side.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "http://snomed.info/sct",
            "code": "44054006",
            "targetsystem": "http://hl7.org/fhir/sid/icd-10-cm/",
        },
    )
    body = r.json()
    if body.get("resourceType") == "OperationOutcome":
        pytest.skip("fixture DB missing the test code")
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    if not matches:
        pytest.skip("no matches for the test code")
    for m in matches:
        concept_part = next(
            (part for part in m.get("part", []) if part.get("name") == "concept"),
            None,
        )
        assert concept_part is not None
        target_system = concept_part.get("valueCoding", {}).get("system")
        assert target_system == "http://hl7.org/fhir/sid/icd-10-cm", (
            f"target.system={target_system!r}; canonical should drop the "
            f"trailing slash from the alias."
        )


# ---------------------------------------------------------------------------
# Lens 4: ConceptMap.url READ/SEARCH (item 5-6).
# Per TS-01 carry-forward, medterm4ds doesn't persist ConceptMap resources.
# READ/SEARCH should return 404 OperationOutcome (not 200 with empty body).
# ---------------------------------------------------------------------------


def test_s40_conceptmap_read_returns_operationoutcome_for_unknown_id(fhir_client):
    """SKEPTIC: GET /fhir/ConceptMap/{id} for any id (medterm4ds doesn't
    persist ConceptMaps) MUST return an OperationOutcome. The shape MUST
    be FHIR-conformant (resourceType=OperationOutcome, severity, code).
    A non-FHIR 404 page (HTML body) would violate §3.1.0.1.5.
    """
    r = fhir_client.get("/fhir/ConceptMap/anything-here")
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"GET /fhir/ConceptMap/{{id}} MUST return OperationOutcome; got "
        f"resourceType={body.get('resourceType')!r}."
    )


def test_s41_conceptmap_search_returns_bundle(fhir_client):
    """SKEPTIC: GET /fhir/ConceptMap (search-type interaction) MUST return
    a Bundle. medterm4ds has no persisted ConceptMaps, so the Bundle
    SHOULD be empty (``total=0``, ``entry=[]``) per FHIR R4 §3.1.0.2.1.
    A non-Bundle shape (e.g. raw OperationOutcome) would be a regression.
    """
    r = fhir_client.get("/fhir/ConceptMap")
    body = r.json()
    assert body.get("resourceType") == "Bundle", (
        f"GET /fhir/ConceptMap MUST return Bundle; got "
        f"resourceType={body.get('resourceType')!r}."
    )


# ---------------------------------------------------------------------------
# Lens 5: group.source / group.target / dependsOn / product (items 2-4).
# Out-of-fixture-scope: medterm4ds ConceptMap export (outputs/fhir.py) does
# not emit dependsOn or product. Document this as INTENDED and verify the
# scoping fields are present in the export shape.
# ---------------------------------------------------------------------------


def test_s50_outputs_fhir_conceptmap_group_has_source_and_target():
    """SKEPTIC: ``concept_map_to_fhir`` in ``outputs/fhir.py`` MUST emit
    ``group[].source`` and ``group[].target`` scoping fields. Per R4 spec
    these are canonical URIs. Per milestone-2 carry-forward, the patient-
    friendly export path exists in ``outputs/fhir.py``; it MUST scope
    groups by source/target system URI pair.
    """
    from medterm4ds.core.models import CodeMapping, CodeRef, ConceptMapRow
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    rows = [
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="Diabetes mellitus",
            target_display="Type 2 diabetes mellitus",
            relationship="source-is-narrower-than-target",
            match_type="broader",
        ),
    ]
    resource = concept_map_to_fhir(rows)
    assert resource["resourceType"] == "ConceptMap"
    groups = resource.get("group", [])
    assert groups, "concept_map_to_fhir MUST emit at least one group"
    for g in groups:
        assert "source" in g, "group missing 'source' scoping field"
        assert "target" in g, "group missing 'target' scoping field"
        assert g["source"] == "http://snomed.info/sct", (
            f"group.source={g['source']!r}; expected canonical SNOMED URI"
        )
        assert g["target"] == "http://hl7.org/fhir/sid/icd-10-cm", (
            f"group.target={g['target']!r}; expected canonical ICD-10-CM URI"
        )


def test_s51_outputs_fhir_conceptmap_url_is_canonical_identifier():
    """SKEPTIC (item 5): ``ConceptMap.url`` is the canonical identifier.
    ``outputs/fhir.py`` defaults to ``urn:medterm4ds:ConceptMap:patient-friendly``.
    Verify the field is present in the export and is a non-empty string.
    """
    from medterm4ds.core.models import CodeRef, ConceptMapRow
    from medterm4ds.outputs.fhir import concept_map_to_fhir, DEFAULT_CONCEPT_MAP_URL

    rows = [
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="DM",
            target_display="T2DM",
            relationship="equivalent",
            match_type="exact",
        )
    ]
    resource = concept_map_to_fhir(rows)
    assert "url" in resource, "ConceptMap export MUST include 'url' field"
    assert isinstance(resource["url"], str) and resource["url"]
    # Default URL exposed as a module-level constant (single source of truth)
    assert DEFAULT_CONCEPT_MAP_URL == resource["url"]


def test_s52_outputs_fhir_depends_on_and_product_out_of_scope_intended():
    """SKEPTIC (items 2-3): ``group.element.target.dependsOn`` and
    ``group.element.target.product`` are out-of-scope for medterm4ds
    (the engine doesn't model parameterized mappings or downstream
    concept derivations). Document this as INTENDED — the absence is
    spec-conformant (both fields have cardinality 0..*).
    """
    from medterm4ds.core.models import CodeRef, ConceptMapRow
    from medterm4ds.outputs.fhir import concept_map_to_fhir

    rows = [
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="DM",
            target_display="T2DM",
            relationship="equivalent",
            match_type="exact",
        )
    ]
    resource = concept_map_to_fhir(rows)
    for g in resource.get("group", []):
        for element in g.get("element", []):
            for target in element.get("target", []):
                # Per R4 spec both are 0..* — absence is conformant.
                if "dependsOn" in target:
                    assert isinstance(target["dependsOn"], list)
                if "product" in target:
                    assert isinstance(target["product"], list)


def test_s53_capability_statement_advertises_conceptmap_interactions():
    """SKEPTIC (item 6): CapabilityStatement MUST advertise the ConceptMap
    resource and the read + search-type interactions per FHIR R4 §3.2.1.0.5.
    Without these, conformant clients cannot discover the resource.
    """
    from medterm4ds.engines.fhir.responses import build_capability_statement

    cs = build_capability_statement(base_url="http://localhost:8001")
    rest = cs.get("rest", [])
    assert rest, "CapabilityStatement MUST have rest[]"
    resources = rest[0].get("resource", [])
    cm = next((r for r in resources if r.get("type") == "ConceptMap"), None)
    assert cm is not None, "CapabilityStatement MUST advertise ConceptMap"
    interactions = {i["code"] for i in cm.get("interaction", [])}
    assert "read" in interactions, (
        f"ConceptMap interactions missing 'read'; got {interactions}"
    )
    assert "search-type" in interactions, (
        f"ConceptMap interactions missing 'search-type'; got {interactions}"
    )
    # The $translate operation MUST be advertised.
    ops = [o.get("name") for o in cm.get("operation", [])]
    assert "translate" in ops, (
        f"ConceptMap operations missing 'translate'; got {ops}"
    )


# ---------------------------------------------------------------------------
# Lens 6: Equivalence enum content pin (item 1 — full enum audit).
# ---------------------------------------------------------------------------


def test_s60_r4_concept_map_equivalence_constant_has_10_values():
    """SKEPTIC: the FHIR R4 ConceptMapEquivalence value set is exactly 10
    values. Per https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html:
      ``relatedto | equivalent | equal | wider | subsumes | narrower |
         specializes | inexact | unmatched | disjoint``
    The frozen-set constant in ``engines/fhir/__init__.py`` is the single
    source of truth imported by both impl and tests (CF-HISTORIAN-VS01-01
    structural fix). Verify cardinality and membership.
    """
    expected = frozenset({
        "relatedto",
        "equivalent",
        "equal",
        "wider",
        "narrower",
        "subsumes",
        "specializes",
        "inexact",
        "unmatched",
        "disjoint",
    })
    assert FHIR_R4_CONCEPT_MAP_EQUIVALENCE == expected, (
        f"FHIR_R4_CONCEPT_MAP_EQUIVALENCE drift: got "
        f"{sorted(FHIR_R4_CONCEPT_MAP_EQUIVALENCE)}; expected "
        f"{sorted(expected)}."
    )


def test_s61_chunk_description_value_list_is_NOT_r4_enum():
    """SKEPTIC: the chunk description for CM-01 listed 12 values:
      ``equal | equivalent | wider | narrower | relatedto | not-relatedto |
         disjoint | subsumes | subsumedby | matches | inexact | unmatched``

    This list is WRONG per R4 spec — it includes R5/R4B values
    (``subsumedby``, ``matches``, ``not-relatedto``) and OMITS the R4
    spec-correct value ``specializes``. This is the same test-suite-
    encoded-wrong-spec meta-pattern that CF-HISTORIAN-VS01-01 surfaced.
    Document the drift so future chunk authors do not copy the wrong list.
    """
    chunk_desc_list = {
        "equal", "equivalent", "wider", "narrower", "relatedto",
        "not-relatedto", "disjoint", "subsumes", "subsumedby",
        "matches", "inexact", "unmatched",
    }
    drift_from_r4 = chunk_desc_list - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert drift_from_r4 == {"not-relatedto", "subsumedby", "matches"}, (
        f"CM-01 chunk description drifted from canonical R4 enum; "
        f"unexpected drift set: {drift_from_r4}"
    )
    # And the chunk desc OMITS the R4 spec-correct value.
    assert "specializes" not in chunk_desc_list, (
        "CM-01 chunk description omits 'specializes' — the R4 spec-correct "
        "value for the reverse-of-subsumes case."
    )


# ---------------------------------------------------------------------------
# Lens 7: build_parameters_translate shape audit — equivalence always present.
# ---------------------------------------------------------------------------


def test_s70_build_parameters_translate_emits_equivalence_for_each_match():
    """SKEPTIC: ``build_parameters_translate`` MUST emit an
    ``equivalence`` part for every ``match`` parameter. Per R4 spec
    (https://hl7.org/fhir/R4/conceptmap-operation-translate.html) the
    match has ``equivalence`` (0..1) and ``concept`` (1..1).
    """
    from medterm4ds.core.models import CodeMapping, CodeRef
    from medterm4ds.engines.fhir.responses import build_parameters_translate

    mappings = [
        CodeMapping(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            relationship="equivalent",
            match_type="exact",
        ),
        CodeMapping(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="RXNORM", code="860975"),
            relationship="source-is-narrower-than-target",
            match_type="broader",
        ),
    ]
    body = build_parameters_translate(
        mappings,
        source_system_uri="http://snomed.info/sct",
        source_code="73211009",
    )
    assert body["resourceType"] == "Parameters"
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    assert len(matches) == 2
    for m in matches:
        part_names = {part.get("name") for part in m.get("part", [])}
        assert "equivalence" in part_names, (
            f"match missing 'equivalence' part; parts={part_names}"
        )
        assert "concept" in part_names, (
            f"match missing 'concept' part; parts={part_names}"
        )
        assert "source" in part_names, (
            f"match missing 'source' part; parts={part_names}"
        )


def test_s71_build_parameters_translate_source_is_narrower_emits_wider_per_r4():
    """SKEPTIC (CRITICAL): the same bug as s20, but exercised at the
    response-builder level. ``build_parameters_translate`` MUST emit
    ``wider`` for an engine relationship of
    ``source-is-narrower-than-target`` (R4 reads equivalence from the
    TARGET perspective: target is wider).

    This probe FAILS today — it is the SKEPTIC reproduction for the
    CRITICAL narrower/wider directionality bug. The fix lands in the
    Remediation phase.
    """
    from medterm4ds.core.models import CodeMapping, CodeRef
    from medterm4ds.engines.fhir.responses import build_parameters_translate

    mappings = [
        CodeMapping(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            relationship="source-is-narrower-than-target",
            match_type="broader",
        ),
    ]
    body = build_parameters_translate(
        mappings,
        source_system_uri="http://snomed.info/sct",
        source_code="73211009",
    )
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    assert matches, "build_parameters_translate emitted no matches"
    equiv_part = next(
        part for part in matches[0]["part"] if part.get("name") == "equivalence"
    )
    equiv = equiv_part.get("valueCode")
    assert equiv == "wider", (
        f"build_parameters_translate emitted equivalence={equiv!r} for "
        f"engine relationship 'source-is-narrower-than-target'. Per R4 spec "
        f"(target perspective), the spec-correct value is 'wider'. "
        f"responses.py:_INTERNAL_REL_TO_FHIR_EQUIVALENCE is INVERTED on "
        f"narrower/wider directionality."
    )


def test_s72_build_parameters_translate_source_is_broader_emits_narrower_per_r4():
    """SKEPTIC (CRITICAL): mirror of s71 for source-is-broader-than-target.
    R4 spec-correct value is ``narrower`` (target is narrower than source).
    """
    from medterm4ds.core.models import CodeMapping, CodeRef
    from medterm4ds.engines.fhir.responses import build_parameters_translate

    mappings = [
        CodeMapping(
            source=CodeRef(source="ICD10CM", code="E11"),
            target=CodeRef(source="SNOMEDCT_US", code="73211009"),
            relationship="source-is-broader-than-target",
            match_type="broader",
        ),
    ]
    body = build_parameters_translate(
        mappings,
        source_system_uri="http://hl7.org/fhir/sid/icd-10-cm",
        source_code="E11",
    )
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    assert matches, "build_parameters_translate emitted no matches"
    equiv_part = next(
        part for part in matches[0]["part"] if part.get("name") == "equivalence"
    )
    equiv = equiv_part.get("valueCode")
    assert equiv == "narrower", (
        f"build_parameters_translate emitted equivalence={equiv!r} for "
        f"engine relationship 'source-is-broader-than-target'. Per R4 spec "
        f"(target perspective), the spec-correct value is 'narrower'. "
        f"responses.py:_INTERNAL_REL_TO_FHIR_EQUIVALENCE is INVERTED on "
        f"narrower/wider directionality."
    )


# ---------------------------------------------------------------------------
# Lens 8: outputs/fhir.py not-translated semantic — additional finding.
# ---------------------------------------------------------------------------


def test_s73_outputs_fhir_not_translated_should_be_unmatched_per_r4():
    """SKEPTIC (HIGH — same root cause family as CM01-SKEPTIC-001):
    ``outputs/fhir.py:FHIR_EQUIVALENCES`` maps ``"not-translated"`` to
    ``"equivalent"``. Per R4 spec
    (https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html):
    ``equivalent`` means "the definitions of the concepts mean the same
    thing". A "not-translated" relationship means there is no
    translation — the source concept has no target-side equivalent. The
    R4 catch-all for "no mapping" is ``unmatched`` (or ``disjoint`` for
    an explicit assertion of non-interchangeability). Mapping it to
    ``equivalent`` is semantically wrong — a client reading the
    ConceptMap export would treat a missing translation as a confirmed
    equivalence, which is a clinical-correctness bug.
    """
    from medterm4ds.outputs.fhir import fhir_equivalence

    result = fhir_equivalence("not-translated")
    assert result == "unmatched", (
        f"outputs/fhir.py FHIR_EQUIVALENCES maps 'not-translated' to "
        f"{result!r}; per R4 spec a missing translation must map to "
        f"'unmatched' (no mapping) or 'disjoint' (explicitly not "
        f"interchangeable), NOT 'equivalent'."
    )

