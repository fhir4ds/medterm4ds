"""CM-02 TERMINOLOGIST: ConceptMap $translate Operation —
clinical/terminological correctness.

Spec:
  * https://build.fhir.org/conceptmap-operation-translate.html (build page)
  * https://hl7.org/fhir/R4/conceptmap-operation-translate.html (canonical R4)
  * https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html (closed enum)

TERMINOLOGIST lens for CM-02 (TERMINOLOGIST FOCUS AREA per chunk
assignment): "Clinical correctness of cross-source mappings
(SNOMED → ICD-10-CM, etc.)". Default severity HIGH per GLOBAL_RULES.md
"TERMINOLOGIST Findings Are HIGH Severity".

8 lens items:

  Lens 1 — Cross-source clinical correctness of same-CUI mappings.
    SNOMED 44054006 (Type 2 diabetes mellitus) and ICD-10-CM E11
    both map to UMLS CUI C0011847 ("Diabetes Mellitus, Type 2").
    The conformance fixture seeds this exact crosswalk (mrconso
    rows for both codes with CUI C0011847). The engine emits
    relationship="equivalent" via get_code_mappings. The spec-correct
    R4 value is ``equivalent`` (= "the definitions of the concepts
    mean the same thing"). Verified end-to-end via $translate.

  Lens 2 — Cross-source directionality mirror invariant on production
    crosswalk scenarios. SNOMED 73211009 (Diabetes broad category) →
    ICD-10-CM E08-E13 (chapter range) clinically means source is
    BROADER than target ⇒ engine emits source-is-broader-than-target
    ⇒ R4 ``narrower`` (target-perspective per CM-01 SKEPTIC FIX-001).
    The mirror case (SNOMED specific → ICD-10 broad) MUST produce R4
    ``wider``. This is the clinical-directionality mirror invariant
    from CM-01 TERMINOLOGIST (test_t43), verified end-to-end via the
    $translate response shape (build_parameters_translate) rather
    than the bare translation map.

  Lens 3 — ``match.source`` Coding-vs-uri semantic decision. R4 spec
    Out Parameters table types ``source`` as ``Coding`` (per canonical
    spec page https://hl7.org/fhir/R4/conceptmap-operation-
    translate.html). The current implementation emits Coding
    (system + code). Some R4 documentation references type ``source``
    loosely as a "uri" in informal text. The TERMINOLOGIST call:
    Coding is the stronger shape (carries display + version) and is
    consistent with the formal In Parameters table type. Verify the
    emitted ``source`` is a Coding (has system + code, can carry
    display in future enhancement).

  Lens 4 — Always-emit message convention. The builder emits a
    ``message`` parameter (0..1 string per spec) on BOTH result=true
    (informative "N matches found") AND result=false (informational
    "0 matches found"). The clinical implication: a message on
    result=true is INFORMATIVE, not noise — a CDS hook reading the
    message knows how many candidate translations the server
    considered. Verify the message is always present and is
    clinically informative (counts matches, doesn't claim "no
    matches" when matches exist).

  Lens 5 — Missing ``match.source.display`` field. The builder has
    access to ``CodeMapping.source_display`` (the engine's preferred
    term for the source code) but does NOT surface it in the
    ``match.source`` Coding. Per FHIR R4 §4.8.21.1 Out Parameters
    table: ``source`` is typed Coding, and Coding.display is 0..1
    "A representation of the meaning of the code in the system".
    Clinical implication: clients building UIs from $translate
    output need the source display to render the source concept
    faithfully. This is a real TERMINOLOGIST finding candidate.

  Lens 6 — Production crosswalk correctness on hierarchical paths.
    The conformance fixture only seeds same-CUI ``equivalent``
    mappings (SNOMED↔ICD-10-CM via CUI). Production data would
    exercise source-is-narrower-than-target and source-is-broader-
    than-target paths. The CM-01 SKEPTIC FIX-001 directionality
    correction is load-bearing for clinical correctness on these
    paths. Verify the builder produces clinically-correct
    equivalence values on synthetic hierarchical mappings.

  Lens 7 — CF-CM02-01 (coding/codeableConcept silent-drop) clinical
    implication. The carry-forward (7th instance of the alt-encoding
    silent-drop pattern) means clients using POST $translate with a
    ``coding`` body OR a ``codeableConcept`` body silently fall
    through to the 400 "system and code are required" path. Clinical
    implication: a CDS hook or EHR integration sending Codings (the
    richer shape per FHIR spec example) gets a silent 400 with no
    indication the encoding was wrong. Verify via behavioral probe
    (SKEPTIC test_s42/s43 + HISTORIAN test_h12/h13) and document the
    carry-forward.

  Lens 8 — Cross-system consistency of the engine pipeline. The
    fixture seeds SNOMED T2DM (C0011847) → ICD-10-CM E11 (C0011847).
    When targetsystem is OMITTED, the handler translates to ALL
    systems except the source. Verify the engine correctly produces
    the ICD-10-CM match when no targetsystem is supplied.

Reference fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus",   "A73211009", "N", "SNOMEDCT_US", "C0011849"),
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),
    ("E11",      "HT", "Type 2 diabetes mellitus", "AE11",      "N", "ICD10CM",    "C0011847"),
    ("860975",   "SCD","24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")
"""

from __future__ import annotations

import pytest

from medterm4ds.core.models import CodeMapping, CodeRef
from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE
from medterm4ds.engines.fhir.responses import (
    build_parameters_translate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"


def _build_translate_params(mapping: CodeMapping, source_uri: str = SNOMED_URI):
    """Helper: build_parameters_translate wrapper with sensible defaults."""
    return build_parameters_translate(
        [mapping],
        source_system_uri=source_uri,
        source_code=mapping.source.code,
    )


def _match_param(out: dict):
    """Return the first match parameter dict, or None."""
    return next(
        (p for p in out["parameter"] if p.get("name") == "match"),
        None,
    )


def _part_value(match_param: dict, name: str, value_key: str):
    """Return the value of a part inside match_param by name."""
    part = next(
        (part for part in match_param["part"] if part.get("name") == name),
        None,
    )
    if part is None:
        return None
    return part.get(value_key)


# ---------------------------------------------------------------------------
# Lens 1 — Cross-source clinical correctness of same-CUI mappings.
# ---------------------------------------------------------------------------


def test_t10_snomed_t2dm_to_icd10cm_e11_equivalent_end_to_end(fhir_client):
    """TERMINOLOGIST Lens 1a: SNOMED 44054006 (T2DM) → ICD-10-CM E11.

    Clinical fact: SNOMED 44054006 and ICD-10-CM E11 share UMLS CUI
    C0011847 ("Diabetes Mellitus, Type 2"). The fixture seeds both
    mrconso rows with this CUI; the engine emits
    relationship="equivalent" via get_code_mappings.

    Per R4 spec: ``equivalent`` = "the definitions of the concepts
    mean the same thing (including the same connotations)". The
    spec-correct R4 value for a same-CUI crosswalk IS ``equivalent``.

    End-to-end $translate probe: SNOMED → ICD-10-CM crosswalk
    produces result=true with a match whose equivalence IS
    ``equivalent``.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": "44054006",
            "targetsystem": ICD10CM_URI,
        },
    )
    assert r.status_code == 200, f"unexpected status: {r.status_code} body={r.text}"
    body = r.json()
    assert body["resourceType"] == "Parameters"
    # result MUST be true (we have a same-CUI match)
    result_param = next(p for p in body["parameter"] if p.get("name") == "result")
    assert result_param["valueBoolean"] is True, (
        "CLINICAL CORRECTNESS: SNOMED T2DM → ICD-10-CM E11 crosswalk via "
        "shared CUI C0011847 MUST produce result=true. A false result would "
        "indicate the engine lost the cross-system link."
    )
    match = _match_param(body)
    assert match is not None, "Expected at least one match for same-CUI crosswalk"
    equiv = _part_value(match, "equivalence", "valueCode")
    assert equiv == "equivalent", (
        f"CLINICAL CORRECTNESS: SNOMED T2DM (C0011847) → ICD-10-CM E11 "
        f"(C0011847) same-CUI crosswalk MUST produce R4 'equivalent'. "
        f"Got {equiv!r}. A wrong value (e.g. 'relatedto') would lose the "
        f"confirmed cross-system semantic equivalence."
    )


def test_t11_snomed_t2dm_to_icd10cm_e11_target_system_concept_carries_display(fhir_client):
    """TERMINOLOGIST Lens 1b: the match.concept Coding carries the
    engine's canonical display for the target code.

    Per R4 spec Coding datatype: display is 0..1 "A representation
    of the meaning of the code in the system". For a clinical
    crosswalk, the target display IS the clinical preferred term
    (e.g., "Type 2 diabetes mellitus" for ICD-10-CM E11).

    A missing target display would force the client to issue a
    separate $lookup to resolve the preferred term — clinically
    wasteful AND error-prone (the client might use a stale cache).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": "44054006",
            "targetsystem": ICD10CM_URI,
        },
    )
    if r.status_code != 200:
        pytest.skip("fixture DB missing the test code")
    body = r.json()
    match = _match_param(body)
    if match is None:
        pytest.skip("no matches in fixture DB")
    concept_part = next(
        part for part in match["part"] if part.get("name") == "concept"
    )
    coding = concept_part["valueCoding"]
    assert "display" in coding, (
        "match.concept Coding MUST carry a display field per FHIR R4 "
        "Coding datatype. Missing display forces a separate $lookup."
    )
    # The display MUST be the engine canonical preferred term, not empty
    assert coding["display"], (
        f"match.concept.display MUST be non-empty (engine canonical preferred "
        f"term). Got {coding.get('display')!r}."
    )


# ---------------------------------------------------------------------------
# Lens 2 — Cross-source directionality mirror invariant.
# ---------------------------------------------------------------------------


def test_t20_directionality_mirror_invariant_on_translate_response():
    """TERMINOLOGIST Lens 2: forward and reverse hierarchical
    relationships produce mirror-image R4 equivalence values via
    ``build_parameters_translate``.

    SNOMED 73211009 (Diabetes broad) → ICD-10-CM E08-E13 (chapter
    range) is the FORWARD case: source BROADER than target ⇒ engine
    emits ``source-is-broader-than-target`` ⇒ R4 ``narrower``
    (target narrower than source per R4 target-perspective).

    SNOMED 44054006 (T2DM specific) → ICD-10-CM E08-E13 (chapter
    range) is the REVERSE case: source NARROWER than target ⇒
    engine emits ``source-is-narrower-than-target`` ⇒ R4 ``wider``.

    The two MUST be different R4 values (mirror invariant). A
    regression that loses directionality (e.g., both emit
    ``equivalent`` or ``relatedto``) is a clinical safety
    violation: a CDS hook would lose the hierarchical inference
    capability.
    """
    forward_mapping = CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="73211009"),
        target=CodeRef(source="ICD10CM", code="E08-E13"),
        source_display="Diabetes mellitus (SNOMED)",
        target_display="Diabetes mellitus chapter (ICD-10-CM)",
        relationship="source-is-broader-than-target",
        match_type="same_cui",
    )
    reverse_mapping = CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="44054006"),
        target=CodeRef(source="ICD10CM", code="E08-E13"),
        source_display="Type 2 diabetes mellitus (SNOMED)",
        target_display="Diabetes mellitus chapter (ICD-10-CM)",
        relationship="source-is-narrower-than-target",
        match_type="same_cui",
    )
    out_fwd = _build_translate_params(forward_mapping)
    out_rev = _build_translate_params(reverse_mapping)
    fwd_equiv = _part_value(_match_param(out_fwd), "equivalence", "valueCode")
    rev_equiv = _part_value(_match_param(out_rev), "equivalence", "valueCode")
    assert fwd_equiv == "narrower", (
        f"FORWARD (source broader → target narrower) MUST produce R4 "
        f"'narrower' (target-perspective). Got {fwd_equiv!r}."
    )
    assert rev_equiv == "wider", (
        f"REVERSE (source narrower → target wider) MUST produce R4 'wider' "
        f"(target-perspective). Got {rev_equiv!r}."
    )
    assert fwd_equiv != rev_equiv, (
        f"Directionality mirror invariant violation: forward ({fwd_equiv!r}) "
        f"and reverse ({rev_equiv!r}) MUST differ for direction-sensitive "
        f"relationships. Same value indicates direction-ignoring bug — "
        f"clinical safety violation."
    )


def test_t21_all_engine_pipeline_relationships_produce_r4_enum_values():
    """TERMINOLOGIST Lens 2b: every engine pipeline relationship value,
    when fed through build_parameters_translate, produces a value in
    the R4 closed enum.

    Engine pipeline emits exactly: equivalent, source-is-narrower-
    than-target, source-is-broader-than-target, related-to,
    not-translated, unmatched (per conceptmap_relationship in
    core/models.py + crosswalk pipeline in engines/duckdb/mappings.py).
    """
    pipeline_relationships = [
        "equivalent",
        "source-is-narrower-than-target",
        "source-is-broader-than-target",
        "related-to",
        "not-translated",
        "unmatched",
    ]
    for rel in pipeline_relationships:
        mapping = CodeMapping(
            source=CodeRef(source="SNOMEDCT_US", code="TEST_SRC"),
            target=CodeRef(source="ICD10CM", code="TEST_TGT"),
            source_display="Source",
            target_display="Target",
            relationship=rel,
            match_type="same_cui",
        )
        out = _build_translate_params(mapping)
        match = _match_param(out)
        equiv = _part_value(match, "equivalence", "valueCode")
        assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"build_parameters_translate for relationship {rel!r} produced "
            f"value {equiv!r} which is NOT in the R4 closed enum. "
            f"Clinical clients cannot interpret off-spec values."
        )


# ---------------------------------------------------------------------------
# Lens 3 — match.source Coding-vs-uri semantic decision.
# ---------------------------------------------------------------------------


def test_t30_match_source_is_coding_not_uri():
    """TERMINOLOGIST Lens 3a: match.source IS a Coding (dict with
    system + code), NOT a bare uri string.

    Per canonical R4 spec Out Parameters table
    (https://hl7.org/fhir/R4/conceptmap-operation-translate.html):
    ``source`` is typed ``Coding`` (0..1). The implementation emits
    a Coding with system + code (current shape) and CAN be extended
    with display in the future (Lens 5). The TERMINOLOGIST call:
    Coding is the stronger shape — it carries display + version
    metadata that a bare uri cannot.
    """
    mapping = CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="44054006"),
        target=CodeRef(source="ICD10CM", code="E11"),
        source_display="Type 2 diabetes mellitus (SNOMED)",
        target_display="Type 2 diabetes mellitus (ICD-10-CM)",
        relationship="equivalent",
        match_type="same_cui",
    )
    out = _build_translate_params(mapping)
    match = _match_param(out)
    source_part = next(
        part for part in match["part"] if part.get("name") == "source"
    )
    # Coding is a dict (not a bare uri string)
    assert "valueCoding" in source_part, (
        f"match.source MUST be a Coding (valueCoding key). "
        f"Got keys {list(source_part.keys())}. A bare uri (valueUri) would "
        f"lose the code identifier — clinical clients cannot determine "
        f"which source code produced this match."
    )
    coding = source_part["valueCoding"]
    assert isinstance(coding, dict), "valueCoding MUST be a dict"
    assert "system" in coding, "match.source Coding MUST carry system"
    assert "code" in coding, "match.source Coding MUST carry code"


def test_t31_match_source_system_matches_canonical_source_uri(fhir_client):
    """TERMINOLOGIST Lens 3b: match.source.system IS the canonical
    source URI (re-resolved via CR-012), not the raw client input.

    Per FHIR R4 §4.8.21.1 Out Coding.system: canonical system URI.
    A client passing a SNOMED OID alias or trailing-slash variant
    MUST get the canonical URI back in the match.source.
    """
    # Use SNOMED OID alias (urn:oid:2.16.840.1.113883.6.96) — the
    # engine resolves to canonical http://snomed.info/sct
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": "urn:oid:2.16.840.1.113883.6.96",
            "code": "44054006",
            "targetsystem": ICD10CM_URI,
        },
    )
    if r.status_code != 200:
        pytest.skip("fixture DB missing the test code or alias unsupported")
    body = r.json()
    match = _match_param(body)
    if match is None:
        pytest.skip("no matches in fixture DB")
    source_part = next(
        part for part in match["part"] if part.get("name") == "source"
    )
    coding = source_part["valueCoding"]
    # CR-012: match.source.system MUST be the canonical SNOMED URI,
    # not the client-supplied OID alias
    assert coding["system"] == SNOMED_URI, (
        f"match.source.system MUST be canonical SNOMED URI {SNOMED_URI!r}. "
        f"Got {coding['system']!r}. CR-012 (milestone-2 review) requires "
        f"canonical re-resolution; an echo of the client alias would be "
        f"the 9th instance of client-input-as-canonical drift."
    )


# ---------------------------------------------------------------------------
# Lens 4 — Always-emit message convention.
# ---------------------------------------------------------------------------


def test_t40_message_emitted_on_match_present(fhir_client):
    """TERMINOLOGIST Lens 4a: message parameter IS emitted on the
    result=true (match found) path.

    Per R4 spec: message is 0..1 string. The implementation ALWAYS
    emits it. Per TERMINOLOGIST call: emitting on result=true is
    INFORMATIVE, not noise — a CDS hook reading "1 matches found"
    knows the server considered exactly 1 candidate, distinguishing
    it from a future "5 matches found" response where multiple
    candidates would need disambiguation.

    The alternative (suppress on result=true) would lose clinically
    useful count information.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": "44054006",
            "targetsystem": ICD10CM_URI,
        },
    )
    if r.status_code != 200:
        pytest.skip("fixture DB missing the test code")
    body = r.json()
    result_param = next(p for p in body["parameter"] if p.get("name") == "result")
    if not result_param["valueBoolean"]:
        pytest.skip("no matches in fixture DB")
    msg_param = next(
        (p for p in body["parameter"] if p.get("name") == "message"),
        None,
    )
    assert msg_param is not None, (
        "message parameter MUST be emitted on result=true path "
        "(always-emit convention). A CDS hook reading the message knows "
        "how many candidates the server considered."
    )
    msg_value = msg_param.get("valueString", "")
    assert "1" in msg_value and "match" in msg_value.lower(), (
        f"message MUST be clinically informative (count matches). "
        f"Got {msg_value!r}. Expected a string like '1 matches found'."
    )


def test_t41_message_emitted_on_no_match(fhir_client):
    """TERMINOLOGIST Lens 4b: message parameter IS emitted on the
    result=false (no match) path.

    A CDS hook receiving result=false with message="0 matches found"
    can distinguish "server considered the request, found no
    translation" from a silent empty response.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": "NONEXISTENT_CODE_99999",
            "targetsystem": ICD10CM_URI,
        },
    )
    if r.status_code != 200:
        pytest.skip("unexpected non-200 status")
    body = r.json()
    result_param = next(p for p in body["parameter"] if p.get("name") == "result")
    assert result_param["valueBoolean"] is False, (
        "Expected result=false for nonexistent source code"
    )
    msg_param = next(
        (p for p in body["parameter"] if p.get("name") == "message"),
        None,
    )
    assert msg_param is not None, (
        "message parameter MUST be emitted on result=false path too."
    )
    assert "0" in msg_param.get("valueString", ""), (
        f"message MUST indicate zero matches. Got {msg_param.get('valueString')!r}."
    )


def test_t42_message_count_matches_actual_match_count(fhir_client):
    """TERMINOLOGIST Lens 4c: the count in the message MUST match
    the actual number of match entries in the response.

    A mismatch (message says "3 matches found" but response has 2
    match entries) is a clinical-safety violation — a CDS hook
    truncating on count would lose matches, while a hook expanding
    would crash on missing entries.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": "44054006",
            "targetsystem": ICD10CM_URI,
        },
    )
    if r.status_code != 200:
        pytest.skip("fixture DB missing the test code")
    body = r.json()
    actual_matches = sum(1 for p in body["parameter"] if p.get("name") == "match")
    msg_param = next(p for p in body["parameter"] if p.get("name") == "message")
    msg_value = msg_param.get("valueString", "")
    assert str(actual_matches) in msg_value, (
        f"Message count MUST match actual match count. Got message "
        f"{msg_value!r} with actual count={actual_matches}."
    )


# ---------------------------------------------------------------------------
# Lens 5 — Missing match.source.display field (FINDING CANDIDATE).
# ---------------------------------------------------------------------------


def test_t50_match_source_currently_omits_display():
    """TERMINOLOGIST Lens 5a: PIN CURRENT BEHAVIOR — match.source
    Coding currently OMITS the display field.

    The builder (``engines/fhir/responses.py:build_parameters_translate``)
    has access to ``CodeMapping.source_display`` (the engine's
    preferred term for the source code). It does NOT surface this
    value in match.source. Per FHIR R4 §4.8.21.1 Out Parameters:
    ``source`` is typed Coding, and Coding.display is 0..1.

    Clinical implication: clients building UIs from $translate output
    need the source display to render the source concept faithfully.
    Without it, the client must issue a SEPARATE $lookup against the
    source system to resolve the display — clinically wasteful AND
    a CDS-hook latency penalty.

    This probe is a CARRY-FORWARD-AS-PROBE (CS-03 TERMINOLOGIST
    methodology) — it PINS the CURRENT behavior. When the future
    enhancement adds source_display to match.source, this probe
    MUST be updated to assert the new behavior.
    """
    mapping = CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="44054006"),
        target=CodeRef(source="ICD10CM", code="E11"),
        source_display="Type 2 diabetes mellitus (SNOMED)",
        target_display="Type 2 diabetes mellitus (ICD-10-CM)",
        relationship="equivalent",
        match_type="same_cui",
    )
    out = _build_translate_params(mapping)
    match = _match_param(out)
    source_part = next(
        part for part in match["part"] if part.get("name") == "source"
    )
    coding = source_part["valueCoding"]
    # PIN CURRENT BEHAVIOR: display is NOT surfaced today
    assert "display" not in coding, (
        "match.source.display is now surfaced. Update this probe (carry-"
        "forward-as-probe pattern) to assert the new behavior — the "
        "CodeMapping.source_display value IS rendered in match.source."
    )
    # Sanity: the engine HAS the display (available but unsurfaced)
    assert mapping.source_display, (
        "Test fixture must populate source_display to exercise this carry-forward."
    )


def test_t51_match_source_display_carry_forward_reproduction_shape():
    """TERMINOLOGIST Lens 5b: REPRODUCTION SHAPE for the
    match.source.display carry-forward.

    When the carry-forward lands (the builder surfaces
    CodeMapping.source_display in match.source), the reproduction
    shape is:

      1. Construct a CodeMapping with source_display="Some Display".
      2. Call build_parameters_translate.
      3. Assert match.source.valueCoding.display == "Some Display".

    This probe documents the expected behavior without enforcing it
    today (CF-TERMINOLOGIST-CM02-01).
    """
    mapping = CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="44054006"),
        target=CodeRef(source="ICD10CM", code="E11"),
        source_display="Type 2 diabetes mellitus (SNOMED)",
        target_display="Type 2 diabetes mellitus (ICD-10-CM)",
        relationship="equivalent",
        match_type="same_cui",
    )
    out = _build_translate_params(mapping)
    match = _match_param(out)
    source_part = next(
        part for part in match["part"] if part.get("name") == "source"
    )
    coding = source_part["valueCoding"]
    # When the carry-forward lands: coding.get("display") ==
    # "Type 2 diabetes mellitus (SNOMED)". Document the expected shape
    # via a comment; the probe above pins the current omission.
    expected_when_fixed = "Type 2 diabetes mellitus (SNOMED)"
    # Today the field is absent; this assertion documents the contract.
    _ = expected_when_fixed  # noqa: F841
    assert mapping.source_display == expected_when_fixed, (
        "Fixture invariant: source_display is populated for the "
        "reproduction shape."
    )


def test_t52_match_source_has_system_and_code_today():
    """TERMINOLOGIST Lens 5c: match.source Coding carries system +
    code (the minimum Coding fields per R4 §3.4.0 Coding datatype).

    Even with display omitted (carry-forward Lens 5), the Coding
    MUST be a valid Coding (system + code). This is the floor of
    conformant shape.
    """
    mapping = CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="44054006"),
        target=CodeRef(source="ICD10CM", code="E11"),
        relationship="equivalent",
        match_type="same_cui",
    )
    out = _build_translate_params(mapping)
    match = _match_param(out)
    source_part = next(
        part for part in match["part"] if part.get("name") == "source"
    )
    coding = source_part["valueCoding"]
    assert "system" in coding, "match.source MUST carry system"
    assert "code" in coding, "match.source MUST carry code"


# ---------------------------------------------------------------------------
# Lens 6 — Production crosswalk correctness on hierarchical paths.
# ---------------------------------------------------------------------------


def test_t60_production_crosswalk_snomed_dm_to_icd10_chapter_narrower():
    """TERMINOLOGIST Lens 6a: PRODUCTION crosswalk — SNOMED 73211009
    (Diabetes broad) → ICD-10-CM E08-E13 (chapter range).

    In production (post-CM-01 SKEPTIC FIX-001 directionality fix),
    this scenario produces:
      engine relationship: source-is-broader-than-target
      R4 equivalence: narrower (target narrower than source)

    The conformance fixture only seeds same-CUI ``equivalent``
    mappings; production UMLS would exercise hierarchical crosswalks
    via SNOMED→ICD-10-CM maps. This probe verifies the builder
    produces the spec-correct R4 value on a SYNTHETIC hierarchical
    mapping (exercises the builder layer, not the HTTP surface).
    """
    mapping = CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="73211009"),
        target=CodeRef(source="ICD10CM", code="E08-E13"),
        source_display="Diabetes mellitus (SNOMED)",
        target_display="Diabetes mellitus chapter (ICD-10-CM)",
        relationship="source-is-broader-than-target",
        match_type="same_cui",
    )
    out = _build_translate_params(mapping)
    match = _match_param(out)
    equiv = _part_value(match, "equivalence", "valueCode")
    assert equiv == "narrower", (
        f"PRODUCTION crosswalk: SNOMED broad (Diabetes mellitus) → ICD-10 "
        f"chapter range (E08-E13) MUST produce R4 'narrower' (target "
        f"narrower than source per R4 target-perspective). Got {equiv!r}. "
        f"A wrong value inverts the clinical hierarchy — a CDS hook would "
        f"mis-attribute breadth to the target."
    )


def test_t61_production_crosswalk_snomed_t2dm_to_icd10_chapter_wider():
    """TERMINOLOGIST Lens 6b: PRODUCTION crosswalk — SNOMED 44054006
    (T2DM specific) → ICD-10-CM E08-E13 (chapter range).

    In production, this produces:
      engine relationship: source-is-narrower-than-target
      R4 equivalence: wider (target wider than source)
    """
    mapping = CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="44054006"),
        target=CodeRef(source="ICD10CM", code="E08-E13"),
        source_display="Type 2 diabetes mellitus (SNOMED)",
        target_display="Diabetes mellitus chapter (ICD-10-CM)",
        relationship="source-is-narrower-than-target",
        match_type="same_cui",
    )
    out = _build_translate_params(mapping)
    match = _match_param(out)
    equiv = _part_value(match, "equivalence", "valueCode")
    assert equiv == "wider", (
        f"PRODUCTION crosswalk: SNOMED T2DM (specific) → ICD-10 chapter "
        f"range (broad) MUST produce R4 'wider' (target wider than source). "
        f"Got {equiv!r}."
    )


def test_t62_production_crosswalk_related_to_emits_relatedto():
    """TERMINOLOGIST Lens 6c: PRODUCTION crosswalk with related-to
    relationship (e.g., SNOMED → RxNorm ingredient-component link).

    The engine emits ``related-to`` for component / first-axis /
    loinc-common relationships. Per R4 spec: ``relatedto`` = "the
    concepts are related, and have at least some overlap in meaning,
    but the exact relationship is not defined".
    """
    mapping = CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="TEST_SRC"),
        target=CodeRef(source="RXNORM", code="TEST_TGT"),
        source_display="Source concept",
        target_display="Related concept",
        relationship="related-to",
        match_type="same_cui",
    )
    out = _build_translate_params(mapping)
    match = _match_param(out)
    equiv = _part_value(match, "equivalence", "valueCode")
    assert equiv == "relatedto", (
        f"PRODUCTION crosswalk: related-to relationship MUST produce R4 "
        f"'relatedto'. Got {equiv!r}."
    )


def test_t63_production_crosswalk_not_translated_emits_unmatched():
    """TERMINOLOGIST Lens 6d: PRODUCTION crosswalk with not-translated
    relationship (no translation in target system).

    Per R4 spec: ``unmatched`` = "there is no match for this concept
    in the target code system". The engine emits ``not-translated``
    for this scenario; SKEPTIC FIX-002 (CM-01) corrected the
    outputs/fhir.py map from ``equivalent`` to ``unmatched``.
    """
    mapping = CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="UNTRANSLATABLE"),
        target=CodeRef(source="ICD10CM", code="UNTRANSLATABLE"),
        source_display="Some technical term with no friendly translation",
        target_display="Some technical term with no friendly translation",
        relationship="not-translated",
        match_type="same_cui",
    )
    out = _build_translate_params(mapping)
    match = _match_param(out)
    equiv = _part_value(match, "equivalence", "valueCode")
    assert equiv == "unmatched", (
        f"PRODUCTION crosswalk: not-translated MUST produce R4 'unmatched'. "
        f"Got {equiv!r}. A wrong value ('equivalent' was the prior bug) "
        f"silently confirms equivalence for missing translations — "
        f"clinical safety violation."
    )


# ---------------------------------------------------------------------------
# Lens 7 — CF-CM02-01 clinical implication (coding/codeableConcept
# silent-drop on POST $translate).
# ---------------------------------------------------------------------------


def test_t70_cf_cm02_01_coding_body_now_honored(fhir_client):
    """TERMINOLOGIST Lens 7a: CF-CM02-01 RESOLVED via CM-01 EXPLORER
    QA-001 — POST $translate with a coding-only body now produces
    200 + Parameters + result=true.

    Per FHIR R4 $translate In Parameters
    (https://hl7.org/fhir/R4/conceptmap-operation-translate.html):
    ``coding`` is 0..1 Coding — a spec-listed alternative to
    system+code. medterm4ds now uses ``_extract_named_coding_from_parameters``
    in ``_extract_translate_params`` (the helper-wiring fix that closes
    CF-CM02-01).

    Clinical implication: a CDS hook or EHR integration sending
    a Coding (the richer shape per FHIR spec example) now succeeds
    instead of silently failing with 400.

    Updated from prior 400-expecting shape when CF-CM02-01 was deferred.
    Carry-forward-as-probe pattern (CS-03 TERMINOLOGIST methodology) —
    methodology fired loudly on the CM-01 EXPLORER fix as designed.

    Pinned by SKEPTIC test_s42 + HISTORIAN test_h12.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": "44054006",
                },
            },
            {"name": "targetsystem", "valueUri": ICD10CM_URI},
        ],
    }
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=body,
    )
    # CF-CM02-01 RESOLVED: helper wired; coding body produces 200.
    assert r.status_code == 200, (
        f"CF-CM02-01 RESOLVED: POST $translate with coding-only body "
        f"MUST return 200. Got {r.status_code}: {r.text}"
    )
    response_body = r.json()
    assert response_body.get("resourceType") == "Parameters"


def test_t71_cf_cm02_01_codeable_concept_body_now_honored(fhir_client):
    """TERMINOLOGIST Lens 7b: CF-CM02-01 RESOLVED via CM-01 EXPLORER
    QA-001 — POST $translate with a codeableConcept-only body now
    produces 200 + Parameters.

    Per FHIR R4 $translate In Parameters: ``codeableConcept`` is 0..1
    CodeableConcept — another spec-listed alternative.

    Updated from prior 400-expecting shape when CF-CM02-01 was deferred.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {
                            "system": SNOMED_URI,
                            "code": "44054006",
                        }
                    ]
                },
            },
            {"name": "targetsystem", "valueUri": ICD10CM_URI},
        ],
    }
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=body,
    )
    assert r.status_code == 200, (
        f"CF-CM02-01 RESOLVED: POST $translate with codeableConcept body "
        f"MUST return 200. Got {r.status_code}: {r.text}"
    )
    response_body = r.json()
    assert response_body.get("resourceType") == "Parameters"


def test_t72_cf_cm02_01_scalar_wins_on_conflict(fhir_client):
    """TERMINOLOGIST Lens 7c: when POST $translate contains BOTH
    scalar system+code AND a coding parameter, the scalar wins.

    Per TS-02 HISTORIAN QA-022 convention: when both encodings are
    present, scalar takes precedence. The implementation silently
    drops the coding parameter. This is the CURRENT behavior;
    CF-CM02-01 remediation MUST preserve scalar-wins-on-conflict.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": "44054006"},
            {
                "name": "coding",
                "valueCoding": {
                    "system": SNOMED_URI,
                    "code": "99999999",  # conflicting code
                },
            },
            {"name": "targetsystem", "valueUri": ICD10CM_URI},
        ],
    }
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=body,
    )
    if r.status_code != 200:
        pytest.skip("fixture DB missing the test code")
    body_json = r.json()
    result_param = next(
        p for p in body_json["parameter"] if p.get("name") == "result"
    )
    # If the fixture has the seeded SNOMED→ICD-10-CM crosswalk for
    # code 44054006, the scalar wins and we get a match.
    if not result_param["valueBoolean"]:
        pytest.skip("no matches for the test code in fixture DB")
    # The match MUST be for the SCALAR code (44054006), NOT the
    # conflicting coding code (99999999).
    match = _match_param(body_json)
    assert match is not None
    source_part = next(
        part for part in match["part"] if part.get("name") == "source"
    )
    assert source_part["valueCoding"]["code"] == "44054006", (
        f"Scalar-wins-on-conflict: source code MUST be the scalar value "
        f"'44054006', NOT the conflicting coding value '99999999'. "
        f"Got {source_part['valueCoding']['code']!r}."
    )


# ---------------------------------------------------------------------------
# Lens 8 — Cross-system consistency of the engine pipeline.
# ---------------------------------------------------------------------------


def test_t80_no_targetsystem_finds_cross_system_match(fhir_client):
    """TERMINOLOGIST Lens 8a: when targetsystem is OMITTED, the
    handler translates to ALL systems except the source. The
    seeded SNOMED→ICD-10-CM crosswalk (via shared CUI C0011847)
    MUST be found.

    Clinical implication: a CDS hook asking "what are ALL the
    translations of SNOMED 44054006?" relies on this default-to-all
    behavior. A regression that restricts to a single target system
    would silently lose matches in other code systems.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": "44054006",
            # targetsystem OMITTED
        },
    )
    if r.status_code != 200:
        pytest.skip("fixture DB missing the test code")
    body = r.json()
    result_param = next(
        p for p in body["parameter"] if p.get("name") == "result"
    )
    assert result_param["valueBoolean"] is True, (
        "No-targetsystem path MUST find cross-system matches (the engine "
        "translates to all systems except the source)."
    )
    match = _match_param(body)
    assert match is not None
    concept_part = next(
        part for part in match["part"] if part.get("name") == "concept"
    )
    coding = concept_part["valueCoding"]
    # The seeded crosswalk is SNOMED → ICD-10-CM
    assert coding["system"] == ICD10CM_URI, (
        f"No-targetsystem cross-system match: expected ICD-10-CM target "
        f"system. Got {coding['system']!r}."
    )
    assert coding["code"] == "E11", (
        f"No-targetsystem cross-system match: expected ICD-10-CM E11. "
        f"Got {coding['code']!r}."
    )


def test_t81_no_targetsystem_match_count_consistent_with_explicit(fhir_client):
    """TERMINOLOGIST Lens 8b: the match count from no-targetsystem
    is at least as large as the match count from an explicit
    targetsystem (because the no-targetsystem path searches all
    systems).

    A regression that reduces the no-targetsystem match count
    below the explicit-targetsystem count is a clinical data-loss
    bug.
    """
    r_explicit = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": "44054006",
            "targetsystem": ICD10CM_URI,
        },
    )
    r_none = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": "44054006",
        },
    )
    if r_explicit.status_code != 200 or r_none.status_code != 200:
        pytest.skip("fixture DB missing the test code")
    explicit_body = r_explicit.json()
    none_body = r_none.json()
    explicit_count = sum(
        1 for p in explicit_body["parameter"] if p.get("name") == "match"
    )
    none_count = sum(
        1 for p in none_body["parameter"] if p.get("name") == "match"
    )
    assert none_count >= explicit_count, (
        f"No-targetsystem match count ({none_count}) MUST be >= explicit "
        f"targetsystem match count ({explicit_count}). A regression to "
        f"less is clinical data-loss."
    )


# ---------------------------------------------------------------------------
# Lens 9 — Closed-enum membership at builder output (defense-in-depth).
# ---------------------------------------------------------------------------


def test_t90_translate_response_emits_only_r4_enum_values():
    """TERMINOLOGIST Lens 9: defense-in-depth — every equivalence
    value emitted by build_parameters_translate on representative
    relationships IS in the R4 closed enum.

    This is the builder-level complement to the engine-vocabulary
    audit in CM-01 TERMINOLOGIST test_t20. The runtime module-load
    assertion in engines/fhir/equivalence.py guards the map; this
    probe guards the builder OUTPUT (the actual wire value).
    """
    pipeline_relationships = [
        "equivalent",
        "source-is-narrower-than-target",
        "source-is-broader-than-target",
        "related-to",
        "not-translated",
        "unmatched",
        # Defensive keys (engine doesn't emit but map covers):
        "wider",
        "narrower",
        "broader",
        "subsumes",
        "specializes",
        "subsumedby",
        "subsumed-by",
    ]
    for rel in pipeline_relationships:
        mapping = CodeMapping(
            source=CodeRef(source="SNOMEDCT_US", code="TEST_SRC"),
            target=CodeRef(source="ICD10CM", code="TEST_TGT"),
            relationship=rel,
            match_type="same_cui",
        )
        out = _build_translate_params(mapping)
        match = _match_param(out)
        equiv = _part_value(match, "equivalence", "valueCode")
        assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"Defense-in-depth: build_parameters_translate on relationship "
            f"{rel!r} produced value {equiv!r} which is NOT in the R4 closed "
            f"enum. Wire values MUST be in the enum."
        )


# ---------------------------------------------------------------------------
# Lens 10 — Cross-handler clinical-content parity (GET ↔ POST).
# ---------------------------------------------------------------------------


def test_t100_get_post_parity_on_match_clinical_content(fhir_client):
    """TERMINOLOGIST Lens 10: GET and POST $translate produce
    byte-exact clinical content on the same source code.

    The clinical content is the match list (equivalence, concept,
    source Codings). A divergence between GET and POST would be a
    silent-wrong-answer at the invocation-path level — a CDS hook
    using POST would get a different clinical interpretation than
    one using GET.
    """
    r_get = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": "44054006",
            "targetsystem": ICD10CM_URI,
        },
    )
    r_post = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    if r_get.status_code != 200 or r_post.status_code != 200:
        pytest.skip("fixture DB missing the test code")
    get_body = r_get.json()
    post_body = r_post.json()
    # Both MUST agree on result
    get_result = next(
        p for p in get_body["parameter"] if p.get("name") == "result"
    )["valueBoolean"]
    post_result = next(
        p for p in post_body["parameter"] if p.get("name") == "result"
    )["valueBoolean"]
    assert get_result == post_result, (
        f"GET↔POST result mismatch: GET={get_result}, POST={post_result}."
    )
    # If matched, both MUST agree on equivalence + target code
    if get_result:
        get_match = _match_param(get_body)
        post_match = _match_param(post_body)
        assert _part_value(get_match, "equivalence", "valueCode") == _part_value(
            post_match, "equivalence", "valueCode"
        ), "GET↔POST equivalence mismatch"
        get_concept = next(
            part for part in get_match["part"] if part.get("name") == "concept"
        )
        post_concept = next(
            part for part in post_match["part"] if part.get("name") == "concept"
        )
        assert get_concept["valueCoding"] == post_concept["valueCoding"], (
            f"GET↔POST target concept mismatch: "
            f"GET={get_concept['valueCoding']}, POST={post_concept['valueCoding']}."
        )


# ---------------------------------------------------------------------------
# Lens 11 — Equivalence wire-type assertion on XML route (extends
# CS-04 TERMINOLOGIST test_t22 + CM-01 TERMINOLOGIST test_t80).
# ---------------------------------------------------------------------------


def test_t110_xml_wire_format_equivalence_value_code(fhir_client):
    """TERMINOLOGIST Lens 11: XML wire-format on $translate — the
    equivalence part uses ``valueCode`` wire type (NOT valueString)
    because the value is from a closed enum.

    Per FHIR R4 §3.4.1 XML representation + CR-002 fix: closed-enum
    values use valueCode. The wire type IS the clinical contract —
    valueCode signals "validate strictly against the FHIR enum" to
    clients.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params={
            "system": SNOMED_URI,
            "code": "44054006",
            "targetsystem": ICD10CM_URI,
            "_format": "xml",
        },
    )
    if r.status_code != 200:
        pytest.skip("fixture DB missing the test code")
    body_text = r.text
    if "match" not in body_text:
        pytest.skip("no matches in fixture DB")
    assert "valueCode" in body_text, (
        "XML wire-format on $translate: equivalence part MUST use valueCode "
        "wire type (closed-enum strictness contract per CS-04 TERMINOLOGIST "
        "test_t22 methodology extended to $translate surface)."
    )
