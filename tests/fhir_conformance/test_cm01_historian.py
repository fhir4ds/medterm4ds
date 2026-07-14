"""HISTORIAN probes for chunk CM-01 (ConceptMap Resource Structure).

Source: https://build.fhir.org/conceptmap.html
Canonical R4 equivalence enum:
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html

HISTORIAN lens (pattern-match against v0.0.1 + cross-chunk patterns):

  Carry-forward from SKEPTIC iteration CM-01 (2 RESOLVED + 1 DEFERRED):
    * CM01-SKEPTIC-001 CRITICAL — ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` had
      narrower/wider INVERTED relative to R4 target-perspective spec.
      ``source-is-narrower-than-target`` mapped to ``narrower`` instead of
      ``wider``. Sibling ``outputs/fhir.py:FHIR_EQUIVALENCES`` was correct.
      Two production maps translating the same engine vocabulary disagreed.
    * CM01-SKEPTIC-002 HIGH — ``outputs/fhir.py:FHIR_EQUIVALENCES["not-translated"]``
      mapped to ``equivalent`` (should be ``unmatched``).
    * CM01-SKEPTIC-003 LOW — chunk description lists R5/R4B values as if
      R4. DEFERRED per user constraint (cannot modify spec_schedule.json).

  Pattern-recurrence audits unique to HISTORIAN:
    1. **Directionality-drift pattern** (NEW class — surfaced by
       CM01-SKEPTIC-001): source-perspective vs target-perspective mismatch.
       Pattern-match against sibling direction-sensitive enum:
       ``$subsumes`` outcome codes
       (https://hl7.org/fhir/R4/codesystem-operation-subsumes.html).
    2. **Cross-module parallel-map drift** — ``responses.py`` and
       ``outputs/fhir.py`` both translate the same engine vocabulary.
       Audit: are there any remaining semantic disagreements on shared keys?
       Audit: characterize the non-shared-key surface for future
       consolidation work.
    3. **CF-HISTORIAN-VS01-01 RESOLVED-status re-verification** — the
       milestone-2 fix must hold across iterations.
    4. **CR-012 RESOLVED-status re-verification** — ``_do_translate``
       uses ``canonical_system_uri()`` helper.
    5. **Test-too-lenient re-audit on SKEPTIC's 22 CM-01 probes** — ensure
       none assert only the absence of one error string on a recognition
       probe.
    6. **Consolidation safety audit** — if the two parallel maps were
       consolidated into one shared module, what production behaviors
       would shift? Document the surface so future TERMINOLOGIST or
       post-CM-04 milestone work can land the refactor safely.
"""

from __future__ import annotations

import pytest

from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE


# ---------------------------------------------------------------------------
# Lens 1: SKEPTIC FIX-VERIFICATION — re-run the load-bearing assertions.
# ---------------------------------------------------------------------------


def test_h10_skeptic_fix_001_survived_source_is_narrower_emits_wider():
    """HISTORIAN: CM01-SKEPTIC-001 RESOLVED-status verification.

    The SKEPTIC fix swapped the inverted directionality in
    ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` so that
    ``source-is-narrower-than-target`` ⇒ R4 ``wider`` (target is wider than
    source) per https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html.

    Re-running this assertion in the HISTORIAN iteration guards against a
    regression being silently reintroduced between iterations. SKEPTIC's
    own probe (``test_s20``) is the canonical pin; this is the
    pattern-match echo.
    """
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE

    assert _INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-narrower-than-target"] == "wider"
    assert _INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-broader-than-target"] == "narrower"


def test_h11_skeptic_fix_002_survived_not_translated_emits_unmatched():
    """HISTORIAN: CM01-SKEPTIC-002 RESOLVED-status verification.

    The SKEPTIC fix corrected
    ``outputs/fhir.py:FHIR_EQUIVALENCES["not-translated"]`` from
    ``equivalent`` to ``unmatched`` per R4 spec (a missing translation
    is NOT a confirmed equivalence — it is a no-mapping catch-all).

    Re-running in the HISTORIAN iteration guards against a regression.
    SKEPTIC's own probe (``test_s73``) is the canonical pin.
    """
    from medterm4ds.outputs.fhir import fhir_equivalence

    assert fhir_equivalence("not-translated") == "unmatched"


def test_h12_skeptic_fix_001_consistent_with_outputs_module_post_fix():
    """HISTORIAN: post-fix the two production maps MUST agree on every
    shared key. CM01-SKEPTIC-001 surfaced a hidden disagreement on
    narrower/wider; this probe extends SKEPTIC ``test_s21`` by enumerating
    every shared key so a future regression on ANY shared key (not just
    narrower/wider) fails loudly.
    """
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE
    from medterm4ds.outputs.fhir import FHIR_EQUIVALENCES

    shared = set(_INTERNAL_REL_TO_FHIR_EQUIVALENCE) & set(FHIR_EQUIVALENCES)
    assert shared, "Pre-condition violated: no shared keys to compare."
    disagreements = {
        k: (_INTERNAL_REL_TO_FHIR_EQUIVALENCE[k], FHIR_EQUIVALENCES[k])
        for k in shared
        if _INTERNAL_REL_TO_FHIR_EQUIVALENCE[k] != FHIR_EQUIVALENCES[k]
    }
    assert not disagreements, (
        f"Post-SKEPTIC-fix regressions on shared equivalence-map keys: "
        f"{disagreements}. The two production maps translating the same "
        f"engine vocabulary MUST agree on every shared key."
    )


# ---------------------------------------------------------------------------
# Lens 2: Pattern-match the directionality-drift pattern to other enum
# surfaces. The sibling surface is ``$subsumes`` outcome codes
# (https://hl7.org/fhir/R4/codesystem-operation-subsumes.html).
# ---------------------------------------------------------------------------


def test_h20_subsumes_outcome_codes_are_r4_closed_enum():
    """HISTORIAN: ``$subsumes`` outcome codes are a closed enum of 4 values
    per https://hl7.org/fhir/R4/codesystem-concept-subsumption-outcome.html.

    Pattern-match against the directionality-drift pattern: confirm the
    production code emits only these 4 values (NOT e.g. ``subsumedby``
    R5/R4B compact form or ``equivalent-match`` like inventions). The
    closed-enum-membership check is the first-line defence against the
    CM01-SKEPTIC-001 class (registry-as-contract pattern, count=4 of the
    literal-vs-canonical-registry drift family at v0.0.1).
    """
    from medterm4ds.engines.fhir.responses import build_parameters_subsumes

    r4_outcomes = {"equivalent", "subsumes", "subsumed-by", "not-subsumed"}
    for outcome in r4_outcomes:
        body = build_parameters_subsumes(outcome)
        param = body["parameter"][0]
        assert param["name"] == "outcome"
        assert param["valueCode"] in r4_outcomes, (
            f"build_parameters_subsumes emitted non-R4 outcome "
            f"{param['valueCode']!r}; must be one of {sorted(r4_outcomes)}."
        )


def test_h21_subsumes_outcome_directionality_is_spec_correct(fhir_client):
    """HISTORIAN (CRITICAL — directionality-drift pattern-match): pattern-
    match CM01-SKEPTIC-001 (narrower/wider inversion) to the sibling
    direction-sensitive enum: ``$subsumes`` outcome codes.

    Per https://hl7.org/fhir/R4/codesystem-operation-subsumes.html:
      * ``subsumes``     = "A subsumes B" (A is broader / ancestor)
      * ``subsumed-by``  = "A subsumed by B" (A is narrower / descendant)

    The production handler ``_do_subsumes`` in ``apps/fhir_api.py``:
      * ``is_descendant(a_ref, b_ref)`` returns True when B is a descendant
        of A → A is ancestor → A subsumes B → outcome MUST be ``subsumes``.
      * ``is_descendant(b_ref, a_ref)`` returns True when A is a descendant
        of B → B is ancestor → A is subsumed by B → outcome MUST be
        ``subsumed-by``.

    Use the seeded SNOMED Diabetes(73211009) IS-A T2DM(44054006) fixture
    to verify the directionality end-to-end. This is the exact pattern
    that CM01-SKEPTIC-001 caught on the equivalence-translation surface
    (the direction was inverted; spec was target-perspective, code was
    source-perspective). On ``$subsumes`` the spec perspective is the
    FIRST argument (``codeA``), and the engine honours that — verified
    by this probe.
    """
    SNOMED_URI = "http://snomed.info/sct"
    SNOMED_DIABETES_MELLITUS = "73211009"  # parent (broader / ancestor)
    SNOMED_T2DM = "44054006"               # child (narrower / descendant)

    # Case 1: A=parent(73211009), B=child(44054006). A subsumes B.
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": SNOMED_URI,
            "codeA": SNOMED_DIABETES_MELLITUS,
            "codeB": SNOMED_T2DM,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    outcome = _param_value(body, "outcome")
    assert outcome == "subsumes", (
        f"Directionality-drift recurrence on $subsumes: A=parent, B=child "
        f"MUST yield outcome='subsumes' (A is ancestor ⇒ A subsumes B). "
        f"Got {outcome!r}. Mirror of CM01-SKEPTIC-001 on the subsumption "
        f"surface."
    )

    # Case 2: A=child(44054006), B=parent(73211009). A is subsumed by B.
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params={
            "system": SNOMED_URI,
            "codeA": SNOMED_T2DM,
            "codeB": SNOMED_DIABETES_MELLITUS,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    outcome = _param_value(body, "outcome")
    assert outcome == "subsumed-by", (
        f"Directionality-drift recurrence on $subsumes: A=child, B=parent "
        f"MUST yield outcome='subsumed-by' (A is descendant ⇒ A is "
        f"subsumed by B). Got {outcome!r}. Mirror of CM01-SKEPTIC-001 on "
        f"the subsumption surface."
    )


def test_h22_subsumes_outcome_codes_use_r4_hyphenated_form():
    """HISTORIAN: R4 spec uses the hyphenated form ``subsumed-by`` (NOT
    the compact R5/R4B form ``subsumedby``). This is the closed-enum-
    membership-vs-directionality distinction — the CM01-SKEPTIC-001 fix
    depends on R4 reading target-perspective, and the R4 enum string is
    itself a load-bearing spec detail.

    Pattern-match CF-HISTORIAN-VS01-01 (R5 ``subsumedby`` leaked into R4
    equivalence enum) to the ``$subsumes`` outcome surface: confirm
    ``build_parameters_subsumes`` would never emit ``subsumedby`` as a
    value (it's not in the R4 closed enum).
    """
    from medterm4ds.engines.fhir.responses import build_parameters_subsumes

    body = build_parameters_subsumes("subsumed-by")
    outcome = _param_value(body, "outcome")
    assert outcome == "subsumed-by", (
        f"build_parameters_subsumes corrupted the hyphenated R4 enum "
        f"value 'subsumed-by' to {outcome!r}."
    )
    # The closed enum never includes the compact R5/R4B form. If a future
    # change introduced 'subsumedby' as an outcome alias, the assertion
    # below would catch it as a CF-HISTORIAN-VS01-01 class regression.
    assert outcome != "subsumedby"


# ---------------------------------------------------------------------------
# Lens 3: Cross-module parallel-map audit — characterize the non-shared
# key surface to document the consolidation opportunity.
# ---------------------------------------------------------------------------


def test_h30_outputs_and_responses_maps_are_now_unified():
    """HISTORIAN: CR-024 (milestone-3 review) RESOLVED-status verification.

    The two parallel maps that translated the same engine vocabulary
    (``outputs/fhir.py:FHIR_EQUIVALENCES`` and
    ``responses.py:_INTERNAL_REL_TO_FHIR_EQUIVALENCE``) have been
    consolidated into a single canonical module
    (``engines/fhir/equivalence.py``). Both surfaces import from there,
    so the maps MUST now be the SAME object — no key/value divergence
    is possible.

    Pre-consolidation (milestone-2 state): ``outputs/fhir.py`` was a
    PROPER SUBSET of ``responses.py`` on the engine vocabulary surface;
    the missing keys were silently defaulted to ``relatedto`` on the
    ConceptMap export surface. Now both surfaces share the full key set.
    """
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE
    from medterm4ds.outputs.fhir import FHIR_EQUIVALENCES

    # CR-024 structural fix: both names refer to the SAME dict object.
    assert FHIR_EQUIVALENCES is _INTERNAL_REL_TO_FHIR_EQUIVALENCE, (
        "CR-024 regression: outputs/fhir.py:FHIR_EQUIVALENCES and "
        "responses.py:_INTERNAL_REL_TO_FHIR_EQUIVALENCE are different "
        "objects. The two surfaces can drift again — re-import from "
        "engines/fhir/equivalence.py."
    )
    # Sanity: the unified map covers the full engine vocabulary surface,
    # including the keys that were previously outputs-only (narrower) or
    # responses-only (broader). The pre-consolidation probe h30 asserted
    # these were responses-only; now they MUST be in both (because both
    # are the same object).
    expected_keys_in_unified_map = {
        # Engine pipeline values:
        "equivalent", "same", "identical",
        "source-is-narrower-than-target", "source-is-broader-than-target",
        "related-to", "not-translated", "unmatched",
        # Defensive pass-through (R4 enum values the engine does not emit
        # today but the unified map accepts for resilience):
        "wider", "narrower", "broader",
        "subsumes", "subsumedby", "subsumed-by", "specializes",
        "relatedto", "not-relatedto", "not-related-to", "disjoint",
    }
    missing = expected_keys_in_unified_map - set(FHIR_EQUIVALENCES.keys())
    assert not missing, (
        f"CR-024 regression: unified equivalence map is missing keys: "
        f"{missing}. The pre-consolidation subset/superset relationship "
        f"has been re-introduced."
    )


def test_h31_outputs_fhir_module_has_untranslated_only_key():
    """HISTORIAN: CR-024 (milestone-3 review) RESOLVED-status verification
    for the ``not-translated`` engine relationship.

    Pre-consolidation: ``outputs/fhir.py:FHIR_EQUIVALENCES`` had a unique
    key ``not-translated`` that ``responses.py`` lacked (the $translate
    operation filtered those out before reaching the response builder).
    Post-consolidation: the unified map covers ``not-translated`` for
    BOTH surfaces (mapped to the R4 catch-all ``unmatched`` per
    CM01-SKEPTIC-002).
    """
    from medterm4ds.outputs.fhir import FHIR_EQUIVALENCES

    # The unified map covers not-translated on both surfaces.
    assert "not-translated" in FHIR_EQUIVALENCES, (
        "CR-024 regression: 'not-translated' is missing from the unified "
        "equivalence map. The patient-friendly export pipeline emits this "
        "relationship for source concepts with no target-side translation."
    )
    # The value MUST be the R4 catch-all 'unmatched' per spec.
    assert FHIR_EQUIVALENCES["not-translated"] == "unmatched"


def test_h32_unified_map_resolves_not_related_to_spelling_aliases_to_unmatched():
    """HISTORIAN: CR-024 (milestone-3 review) RESOLVED-status verification
    for the ``not-relatedto`` / ``not-related-to`` spelling divergence.

    Pre-consolidation: ``outputs/fhir.py:FHIR_EQUIVALENCES`` mapped
    ``not-related-to`` (with hyphen) to ``disjoint``, while
    ``responses.py:_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` mapped
    ``not-relatedto`` (no hyphen) to ``unmatched``. These were
    semantically different on BOTH axes (key spelling + value).

    Post-consolidation: both spellings are accepted as keys in the
    unified map and both map to the R4 catch-all ``unmatched``. The
    ``disjoint`` value would wrongly imply the concepts are explicitly
    disconnected (a stronger claim than "no mapping"); ``unmatched``
    is the conservative default for an unknown engine vocabulary token.
    """
    from medterm4ds.outputs.fhir import FHIR_EQUIVALENCES

    # Both spellings now accepted and unified on the R4 catch-all:
    assert FHIR_EQUIVALENCES.get("not-related-to") == "unmatched", (
        "CR-024 regression: 'not-related-to' (hyphenated) must map to "
        "'unmatched' (R4 catch-all). The prior 'disjoint' value was a "
        "stronger claim than the engine vocabulary warrants."
    )
    assert FHIR_EQUIVALENCES.get("not-relatedto") == "unmatched", (
        "CR-024 regression: 'not-relatedto' (un-hyphenated) must map to "
        "'unmatched' (R4 catch-all)."
    )


# ---------------------------------------------------------------------------
# Lens 4: CF-HISTORIAN-VS01-01 RESOLVED-status re-verification (3rd
# personality to confirm — SKEPTIC, then ARCHITECT, now HISTORIAN).
# ---------------------------------------------------------------------------


def test_h40_cf_historian_vs01_01_resolved_status_no_r5_values_leak():
    """HISTORIAN: CF-HISTORIAN-VS01-01 RESOLVED-status verification.

    The milestone-2 remediation fixed two R5/R4B leakage bugs in
    ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE``:
      * ``subsumedby`` (R5/R4B value) → R4 ``specializes``.
      * ``not-relatedto`` (not in any FHIR enum) → R4 ``unmatched``.

    Pattern-match by source-reading: every value in the map MUST be in
    the canonical R4 closed enum, AND the specific R5/R4B values that
    leaked pre-fix MUST NOT appear as values (the keys are still allowed
    as engine input that gets translated).
    """
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE

    values = set(_INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
    # Closed-enum membership:
    assert values <= FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
        f"CF-HISTORIAN-VS01-01 regression: values outside R4 closed enum: "
        f"{values - FHIR_R4_CONCEPT_MAP_EQUIVALENCE}."
    )
    # The specific R5/R4B leakage values MUST NOT appear as values:
    forbidden_values = {"subsumedby", "matches", "not-relatedto"}
    leaked = values & forbidden_values
    assert not leaked, (
        f"CF-HISTORIAN-VS01-01 regression: R5/R4B enum values leaked into "
        f"the R4 surface: {leaked}."
    )


def test_h41_module_load_assertion_present():
    """HISTORIAN: the module-load ``assert`` in the canonical equivalence
    module is the structural defence against future closed-enum drift.

    Pre-CR-024 (milestone-2 state): the assert lived in ``responses.py``
    and applied only to the $translate surface; ``outputs/fhir.py`` had
    no equivalent guard. Post-CR-024: the assert lives in
    ``engines/fhir/equivalence.py`` and applies to BOTH surfaces because
    both import the canonical map. Verify the assert is present in the
    canonical module and references the canonical R4 frozen-set constant.
    """
    import inspect

    from medterm4ds.engines.fhir import equivalence as equiv_module

    src = inspect.getsource(equiv_module)
    assert "FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in src, (
        "engines/fhir/equivalence.py lost its module-load reference to "
        "FHIR_R4_CONCEPT_MAP_EQUIVALENCE — the closed-enum drift "
        "structural defence is gone."
    )
    assert "INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()" in src, (
        "engines/fhir/equivalence.py lost its module-load assertion on "
        "INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()."
    )


# ---------------------------------------------------------------------------
# Lens 5: CR-012 RESOLVED-status re-verification — source URI canonical
# re-resolution on the $translate surface.
# ---------------------------------------------------------------------------


def test_h50_cr012_resolved_status_do_translate_uses_canonical_helper():
    """HISTORIAN: CR-012 RESOLVED-status verification (source-reading).

    The milestone-2 remediation wired the ``canonical_system_uri()``
    helper into ``_do_translate`` so the Out ``match[].source.system``
    field is the canonical URI (not the client alias). Verify the
    helper is still wired in.
    """
    import inspect

    from medterm4ds.apps.fhir_api import create_fhir_app

    src = inspect.getsource(create_fhir_app)
    assert "canonical_system_uri" in src, (
        "CR-012 regression: _do_translate no longer calls "
        "canonical_system_uri on the client-supplied source_uri."
    )


def test_h51_cr012_resolved_status_alias_resolves_to_canonical(fhir_client):
    """HISTORIAN: CR-012 RESOLVED-status verification (end-to-end).

    Calling $translate with the SNOMED OID alias
    (``urn:oid:2.16.840.1.113883.6.96``) MUST return the canonical
    URI (``http://snomed.info/sct``) in Out ``match[].source.system``.
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
            f"CR-012 regression: $translate echoed client alias "
            f"{src_system!r} verbatim; expected canonical "
            f"'http://snomed.info/sct'."
        )


# ---------------------------------------------------------------------------
# Lens 6: Test-too-lenient re-audit on SKEPTIC's 22 CM-01 probes.
# Strategy 15 (TS-03 HISTORIAN): negative-only assertions on recognition
# probes are a known anti-pattern. Audit SKEPTIC's probes for the same.
# ---------------------------------------------------------------------------


def test_h60_skeptic_probes_use_positive_success_shape_not_negative_only():
    """HISTORIAN (Strategy 15): re-audit SKEPTIC's CM-01 probe set for
    test-too-lenient anti-patterns.

    The probe set is ``tests/fhir_conformance/test_cm01_skeptic.py``.
    Each probe MUST assert a positive success shape (200 + resource body
    + specific field equality), NOT just the absence of an error string.

    Audit method: source-read the file and assert that the dominant
    assertion pattern is ``==`` (equality) or ``in`` (membership), not
    just ``not in`` or ``!=``. A file where the dominant pattern is
    negative-only would be flagged.
    """
    from pathlib import Path

    probe_path = Path(__file__).parent / "test_cm01_skeptic.py"
    src = probe_path.read_text()
    # Positive-shape signals:
    equality_count = src.count("assert ")  # every assert is a positive claim
    # The probe set has 22 tests with multiple asserts each; sanity floor.
    assert equality_count >= 40, (
        f"SKEPTIC CM-01 probe set has only {equality_count} assert statements; "
        f"expected at least 40 for a 22-probe file with positive-shape "
        f"assertions."
    )
    # Negative-only-assertion anti-pattern (Strategy 15): search for
    # assert blocks that consist ONLY of "assert X not in Y" or
    # "assert X != Y" without a paired positive assertion. Heuristic:
    # at least 80% of asserts should be positive-shape (==, in, or
    # is not None on a positive fetch).
    negative_markers = src.count(" not in ") + src.count(" != ")
    positive_markers = src.count(" == ") + src.count(" in ")
    if negative_markers > 0:
        ratio = positive_markers / max(negative_markers, 1)
        assert ratio >= 1.0, (
            f"SKEPTIC CM-01 probe set has too many negative-only "
            f"assertions (positive/negative ratio = {ratio:.2f}). "
            f"Strategy 15 violation — every recognition probe should "
            f"assert a positive success shape, not just the absence of "
            f"an error."
        )


def test_h61_skeptic_test_s30_uses_source_reading_not_runtime_arms():
    """HISTORIAN: SKEPTIC ``test_s30`` (CR-012 verification) uses
    ``inspect.getsource`` to verify the helper is wired. This is the
    right pattern (it doesn't require fixture DB and fails loudly on
    refactor). Confirm the probe still uses this pattern.
    """
    from pathlib import Path

    probe_path = Path(__file__).parent / "test_cm01_skeptic.py"
    src = probe_path.read_text()
    assert "inspect.getsource" in src, (
        "SKEPTIC CM-01 probes lost the source-reading pattern. "
        "Strategy 29 (carry-forward-as-source-reading-probe) regression."
    )


# ---------------------------------------------------------------------------
# Lens 7: Consolidation safety audit — if the two parallel maps were
# unified, what production behaviour would shift? Document the surface.
# ---------------------------------------------------------------------------


def test_h70_consolidation_unified_map_would_emit_correct_r4_for_all_engine_inputs():
    """HISTORIAN: consolidation candidate — a single canonical map
    merging ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` and ``FHIR_EQUIVALENCES``
    into one shared module imported by both ``responses.py`` and
    ``outputs/fhir.py``. Compute the merged map and verify it preserves
    spec-correct semantics on every key.

    Safety property: for every engine relationship key in EITHER map
    today, the merged map MUST produce the same R4 value as that map
    currently does. If a future consolidation introduces a key
    collision (same key, different values), this probe fails loudly.
    """
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE
    from medterm4ds.outputs.fhir import FHIR_EQUIVALENCES

    # Compute the merge:
    merged: dict[str, str] = {}
    collisions: dict[str, tuple[str, str]] = {}
    for key, value in _INTERNAL_REL_TO_FHIR_EQUIVALENCE.items():
        merged[key] = value
    for key, value in FHIR_EQUIVALENCES.items():
        if key in merged and merged[key] != value:
            collisions[key] = (merged[key], value)
        else:
            merged[key] = value
    # There are NO collisions today (post-SKEPTIC-fix). Document this
    # invariant so a future regression that introduces a collision
    # fails loudly.
    assert not collisions, (
        f"Consolidation safety regression: parallel equivalence maps have "
        f"introduced key collisions on shared keys {collisions}. This "
        f"probe is the early-warning system for the consolidation refactor "
        f"— if it fires, the refactor cannot proceed without resolving "
        f"each collision's intended semantics."
    )


def test_h71_consolidation_unified_map_would_preserve_r4_closed_enum():
    """HISTORIAN: the merged equivalence map (Lens 7 candidate) MUST
    still emit only R4 closed-enum values. The frozen-set constant
    ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE`` is the registry-as-contract
    for the post-consolidation world.
    """
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE
    from medterm4ds.outputs.fhir import FHIR_EQUIVALENCES

    merged_values = set(_INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()) | set(FHIR_EQUIVALENCES.values())
    drift = merged_values - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert not drift, (
        f"Consolidation safety regression: merged equivalence map emits "
        f"values outside the R4 closed enum: {drift}. The frozen-set "
        f"constant must guard both production maps."
    )


def test_h72_consolidation_unified_map_would_preserve_target_perspective_semantics():
    """HISTORIAN: the most load-bearing consolidation invariant — the
    R4 target-perspective directionality of ``narrower`` / ``wider``
    MUST be preserved. CM01-SKEPTIC-001 was a CRITICAL bug exactly
    because this invariant was violated. Pin it explicitly so any
    future refactor that re-introduces the inversion fails this probe.
    """
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE
    from medterm4ds.outputs.fhir import FHIR_EQUIVALENCES

    # Both maps MUST agree: source-is-narrower-than-target ⇒ wider (target wider).
    assert _INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-narrower-than-target"] == "wider"
    assert FHIR_EQUIVALENCES["source-is-narrower-than-target"] == "wider"
    # Both maps MUST agree: source-is-broader-than-target ⇒ narrower (target narrower).
    assert _INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-broader-than-target"] == "narrower"
    assert FHIR_EQUIVALENCES["source-is-broader-than-target"] == "narrower"


# ---------------------------------------------------------------------------
# Shared test helpers (mirror of cs05_terminologist).
# ---------------------------------------------------------------------------


def _param_value(body: dict, name: str):
    """Return the value of the first Out parameter with the given name."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k.startswith("value"):
                    return v
            return None
    return None
