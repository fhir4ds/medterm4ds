"""SKEPTIC probes for chunk CM-02 (ConceptMap $translate Operation).

Source: https://build.fhir.org/conceptmap-operation-translate.html
Canonical R4 $translate operation:
    https://hl7.org/fhir/R4/conceptmap-operation-translate.html

Chunk scope (7 items):
  1. Required params: ``sourceCode`` (or ``coding``/``codeableConcept``), ``system``.
  2. Optional params: ``version``, ``sourceScope`` (ValueSet URL),
     ``targetScope`` (ValueSet URL), ``targetSystem``, ``targetCode``,
     ``ConceptMap`` (instance-level), ``reverse``, ``targetPrune``.
  3. Returns Parameters with ``result`` (boolean), ``message`` (string,
     optional), ``match`` (repeating).
  4. Each match contains: ``equivalence``, ``concept`` (Coding), ``source``,
     ``target``, ``dependsOn``, ``product``.
  5. When no mapping: ``result=false``.
  6. When ConceptMap URL specified, only that map is consulted.
  7. When ``targetScope`` specified, mappings constrained to that scope.

SKEPTIC lens (adversarial bug hunting):
  * Required params: drop each of ``sourceCode`` / ``system`` — expect 400.
  * Optional params: ``version``, ``sourceScope``, ``targetScope``,
    ``targetSystem``, ``targetCode``, ``ConceptMap`` (instance-level),
    ``reverse``, ``targetPrune``.
  * Response shape: ``result`` (boolean) always; ``message`` (string,
    optional); ``match`` (repeating) — each has ``equivalence``,
    ``concept``, ``source``, ``target``.
  * Match.equivalence values from FHIR R4 enum (canonical module
    post-CR-024): every value MUST be a member of
    ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE``.
  * No mapping case: ``result=false``. Verify message shape.
  * CodeableConcept multi-coding (similar to VS-05 QA-070): any match →
    result=true.
  * Coding parameter (POST body with Coding resource).
  * targetScope constraint (when specified, only matches in that scope
    returned).
  * ConceptMap URL constraint (when specified, only that map consulted).
  * Reverse mode (when reverse=true, direction is reversed).
  * Canonical-URI echo (CR-012 RESOLVED, CR-025 RESOLVED):
    ``_do_translate`` Out ``match.source.system`` should use canonical.
    Verify with alias inputs (urn:oid, trailing-slash).
  * Equivalence vocabulary audit: every match.equivalence from FHIR R4
    enum (via ``engines/fhir/equivalence.py``).

Note: the medterm4ds implementation today only declares ``system``,
``code``, ``targetsystem``, ``source`` (ConceptMap URL — passed through
but not used), ``targetCode`` (declared but not used). Optional
parameters such as ``version``, ``sourceScope``, ``targetScope``,
``reverse``, ``targetPrune``, ``coding``, ``codeableConcept``,
``ConceptMap`` (inline resource), ``conceptMapVersion`` are NOT declared
on the GET handler. The SKEPTIC iteration probes whether the server
ACCEPTS them without 500 (spec-compatibility fallback) — many of these
will be filed as DEFERRED enhancement candidates rather than bugs.

The implementation also emits ``match.source`` as a Coding (with system
+ code) rather than as a bare URI per R4 spec text. Some R4 references
show ``match.source`` typed as a uri rather than Coding; this is filed
as a TERMINOLOGIST carry-forward (semantic ambiguity in R4 spec itself).
"""

from __future__ import annotations

from typing import Any

import pytest

from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
    canonical_system_uri,
    fhir_uri_to_system,
)
from medterm4ds.engines.fhir.equivalence import (
    INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    fhir_equivalence,
)
from medterm4ds.engines.fhir.responses import (
    build_parameters_translate,
    _fhir_equivalence_from_relationship,
)


# ---------------------------------------------------------------------------
# Constants for the probes.
# ---------------------------------------------------------------------------
SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URI_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.96"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
# Plausible ConceptMap canonical URLs (medterm4ds does NOT persist
# ConceptMaps today — the URL is accepted but not used to select a map).
CONCEPTMAP_URL = "http://medterm4ds.org/fhir/ConceptMap/snomed-to-icd10"


def _find_param(body: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Return the first ``parameter`` entry with ``name == name``, else None."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            return p
    return None


def _match_parts(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the list of ``part`` dicts from every ``match`` parameter."""
    out: list[dict[str, Any]] = []
    for p in body.get("parameter", []):
        if p.get("name") == "match":
            for part in p.get("part", []):
                out.append(part)
    return out


# ===========================================================================
# Lens 1: Required parameters — drop each of ``system`` / ``code`` — expect
# 400. (Item 1 of chunk scope.)
# ===========================================================================


def test_s10_get_translate_missing_system_returns_400(fhir_client):
    """SKEPTIC (item 1): GET $translate WITHOUT ``system`` MUST return 400.

    Spec: FHIR R4 $translate requires the source code (``code``) and a
    way to identify the source code system. Without ``system``, the
    server cannot resolve the source. Per the spec's "Parameters" section,
    ``system`` is 0..1 BUT must be present whenever ``code`` is present
    (the spec text says "the code is not valid without the system").

    Adversarial: drop ``system`` only. Expect 400 + OperationOutcome +
    conformant Content-Type.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[("code", "44054006")],
    )
    assert r.status_code == 422 or r.status_code == 400, (
        f"GET $translate missing system — expected 400 or 422; got "
        f"{r.status_code}: {r.text}"
    )
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"Content-Type drift on error path: {r.headers['content-type']!r}; "
        f"expected application/fhir+json."
    )


def test_s11_get_translate_missing_code_returns_400(fhir_client):
    """SKEPTIC (item 1): GET $translate WITHOUT ``code`` MUST return 400.

    Mirror of test_s10 on the ``code`` parameter. Without a source code
    to translate, the operation is meaningless.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[("system", SNOMED_URI)],
    )
    assert r.status_code == 422 or r.status_code == 400, (
        f"GET $translate missing code — expected 400 or 422; got "
        f"{r.status_code}: {r.text}"
    )
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"Content-Type drift on error path: {r.headers['content-type']!r}; "
        f"expected application/fhir+json."
    )


def test_s12_post_translate_missing_system_returns_400(fhir_client):
    """SKEPTIC (item 1): POST $translate WITHOUT ``system`` MUST return 400.

    Spec: same as test_s10 but on the POST route. The ``translate_post``
    handler explicitly checks for ``system`` presence at line 1992.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "code", "valueCode": "44054006"},
            ],
        },
    )
    assert r.status_code == 400, (
        f"POST $translate missing system — expected 400; got "
        f"{r.status_code}: {r.text}"
    )
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"Content-Type drift on error path: {r.headers['content-type']!r}; "
        f"expected application/fhir+json."
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"resourceType drift on error path: {body.get('resourceType')!r}; "
        f"expected OperationOutcome."
    )


def test_s13_post_translate_missing_code_returns_400(fhir_client):
    """SKEPTIC (item 1): POST $translate WITHOUT ``code`` MUST return 400.

    Mirror of test_s12 on the ``code`` parameter.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
            ],
        },
    )
    assert r.status_code == 400, (
        f"POST $translate missing code — expected 400; got "
        f"{r.status_code}: {r.text}"
    )
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"Content-Type drift on error path: {r.headers['content-type']!r}; "
        f"expected application/fhir+json."
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"resourceType drift on error path: {body.get('resourceType')!r}; "
        f"expected OperationOutcome."
    )


# ===========================================================================
# Lens 2: Response shape — ``result`` (boolean), ``message`` (string,
# optional), ``match`` (repeating). (Items 3 and 4 of chunk scope.)
# ===========================================================================


def test_s20_translate_response_has_result_boolean(fhir_client):
    """SKEPTIC (item 3): every $translate response MUST include a
    ``result`` parameter of type boolean.

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
    Out Parameters: ``result`` is 1..1 boolean.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("resourceType") == "Parameters", (
        f"resourceType drift: {body.get('resourceType')!r}; expected Parameters."
    )
    result = _find_param(body, "result")
    assert result is not None, (
        "$translate response missing required 'result' parameter."
    )
    assert "valueBoolean" in result, (
        f"'result' parameter missing valueBoolean key: {result!r}"
    )
    assert isinstance(result["valueBoolean"], bool), (
        f"'result' value is not a Python bool: type={type(result['valueBoolean']).__name__}; "
        f"value={result['valueBoolean']!r}. FHIR boolean primitives MUST be "
        f"lowercase 'true'/'false' on the wire — Python str(True) is 'True' "
        f"(capital T). Reference: GLOBAL_RULES.md boolean-rendering rule."
    )


def test_s21_translate_response_has_message_string(fhir_client):
    """SKEPTIC (item 3): every $translate response from medterm4ds carries
    a ``message`` parameter of type string. Spec says 0..1 (optional); the
    server emits it always. Verify the type is string.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    body = r.json()
    message = _find_param(body, "message")
    assert message is not None, (
        "$translate response missing 'message' parameter."
    )
    assert "valueString" in message, (
        f"'message' parameter missing valueString key: {message!r}"
    )
    assert isinstance(message["valueString"], str), (
        f"'message' value is not a string: type={type(message['valueString']).__name__}"
    )


def test_s22_translate_match_has_required_parts(fhir_client):
    """SKEPTIC (item 4): each ``match`` entry MUST contain the
    spec-required parts: ``equivalence``, ``concept``, ``source``.

    Spec: FHIR R4 Out Parameters lists each match with these required
    parts. ``dependsOn`` and ``product`` are 0..* (omitted when absent,
    per CF-SKEPTIC-VS01 family).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(matches) > 0, (
        "Expected at least one match for SNOMED 44054006 → ICD-10-CM "
        "(fixture seeds T2DM CUI C0011847 in both systems)."
    )
    for match in matches:
        part_names = {part.get("name") for part in match.get("part", [])}
        assert "equivalence" in part_names, (
            f"match missing 'equivalence' part; has: {part_names}"
        )
        assert "concept" in part_names, (
            f"match missing 'concept' part; has: {part_names}"
        )
        assert "source" in part_names, (
            f"match missing 'source' part; has: {part_names}"
        )


def test_s23_translate_match_equivalence_in_r4_enum(fhir_client):
    """SKEPTIC (item 4): every match.equivalence value MUST be a member
    of the FHIR R4 ConceptMapEquivalence closed enum.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    Pinned by the canonical ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE`` constant
    in ``engines/fhir/__init__.py`` (post-CR-014). This probe is the
    runtime-side guard complementing the module-load ``assert`` in
    ``engines/fhir/equivalence.py`` (post-CR-024).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    for match in matches:
        equiv_part = next(
            (p for p in match.get("part", []) if p.get("name") == "equivalence"),
            None,
        )
        assert equiv_part is not None, "match missing equivalence part"
        assert "valueCode" in equiv_part, (
            f"equivalence part missing valueCode: {equiv_part!r}"
        )
        equiv = equiv_part["valueCode"]
        assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"match.equivalence value {equiv!r} is NOT in the FHIR R4 "
            f"ConceptMapEquivalence closed enum. Drift regression of "
            f"CF-HISTORIAN-VS01-01 or sibling equivalence-vocabulary drift."
        )


def test_s24_translate_match_concept_is_coding(fhir_client):
    """SKEPTIC (item 4): match.concept MUST be a Coding with at least
    ``system`` and ``code`` fields.

    Spec: FHIR R4 Out Parameters ``match.concept`` is 0..1 Coding. A
    Coding without ``system`` is meaningless for cross-system translation
    — the client cannot know which code system the target code belongs to.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(matches) > 0
    for match in matches:
        concept_part = next(
            (p for p in match.get("part", []) if p.get("name") == "concept"),
            None,
        )
        assert concept_part is not None, "match missing concept part"
        assert "valueCoding" in concept_part, (
            f"concept part missing valueCoding: {concept_part!r}"
        )
        coding = concept_part["valueCoding"]
        assert "system" in coding, (
            f"match.concept Coding missing 'system' field: {coding!r}"
        )
        assert "code" in coding, (
            f"match.concept Coding missing 'code' field: {coding!r}"
        )
        assert coding["system"] == ICD10CM_URI, (
            f"match.concept.system drift: {coding['system']!r}; expected "
            f"{ICD10CM_URI!r}."
        )
        assert coding["code"] == "E11", (
            f"match.concept.code drift: {coding['code']!r}; expected 'E11' "
            f"(fixture seeds T2DM ICD-10-CM E11 with CUI C0011847)."
        )


def test_s25_translate_match_source_is_coding_with_canonical(fhir_client):
    """SKEPTIC (item 4 + CR-012 RESOLVED): match.source MUST be a Coding
    with ``system`` and ``code``, and the ``system`` MUST be the CANONICAL
    FHIR R4 URI (not an alias or trailing-slash variant).

    Spec: FHIR R4 Out Parameters. CR-012 (milestone-2 review) wrapped
    ``source_uri`` through ``canonical_system_uri`` before passing to the
    response builder. This probe verifies the fix using alias inputs.

    Reference: ``apps/fhir_api.py:_do_translate`` line 2025
    (``canonical_source_uri = canonical_system_uri(source_uri, source=source)``).
    """
    # Use the OID alias for SNOMED CT — CR-012 should resolve it to the
    # canonical URI on the Out match.source.system field.
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI_OID_ALIAS),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(matches) > 0
    for match in matches:
        source_part = next(
            (p for p in match.get("part", []) if p.get("name") == "source"),
            None,
        )
        assert source_part is not None, "match missing source part"
        assert "valueCoding" in source_part, (
            f"source part missing valueCoding: {source_part!r}"
        )
        coding = source_part["valueCoding"]
        assert coding.get("system") == SNOMED_URI, (
            f"match.source.system drift: {coding.get('system')!r}; expected "
            f"canonical {SNOMED_URI!r} (CR-012 should resolve the OID alias)."
        )
        assert coding.get("code") == "44054006", (
            f"match.source.code drift: {coding.get('code')!r}; expected 44054006."
        )


def test_s26_translate_match_source_canonical_trailing_slash(fhir_client):
    """SKEPTIC (CR-012 RESOLVED, sibling of test_s25): trailing-slash
    variant of the SNOMED URI MUST resolve to the canonical URI in
    Out ``match.source.system``.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI_TRAILING_SLASH),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(matches) > 0
    for match in matches:
        source_part = next(
            (p for p in match.get("part", []) if p.get("name") == "source"),
            None,
        )
        coding = source_part["valueCoding"]
        assert coding.get("system") == SNOMED_URI, (
            f"trailing-slash input {SNOMED_URI_TRAILING_SLASH!r} should "
            f"resolve to canonical {SNOMED_URI!r} on Out match.source.system "
            f"(CR-012); got {coding.get('system')!r}."
        )


# ===========================================================================
# Lens 3: No-mapping case — ``result=false``. (Item 5 of chunk scope.)
# ===========================================================================


def test_s30_translate_no_mapping_result_false(fhir_client):
    """SKEPTIC (item 5): when no mapping is found, the response MUST
    carry ``result=false`` and NO ``match`` entries.

    Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
    Out ``result``: "True if the engine was able to return a mapping".

    Adversarial: translate a code that exists in SNOMED but has NO
    cross-system mapping in the fixture. The fixture seeds only one
    cross-CUI mapping (SNOMED 44054006 ↔ ICD-10-CM E11 via CUI C0011847).
    Translating SNOMED 44054006 to RxNorm yields no match.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", RXNORM_URI),
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    body = r.json()
    result = _find_param(body, "result")
    assert result is not None, "missing result parameter on no-match response"
    assert result.get("valueBoolean") is False, (
        f"no-match response MUST have result=false; got {result.get('valueBoolean')!r}."
    )
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(matches) == 0, (
        f"no-match response MUST have zero match entries; got {len(matches)}."
    )


def test_s31_translate_no_mapping_message_shape(fhir_client):
    """SKEPTIC (item 5, sibling of test_s30): the ``message`` parameter
    on a no-match response MUST be a string (even though it's optional).
    Verify the shape.

    Spec: Out ``message`` is 0..1 string.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", RXNORM_URI),
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    body = r.json()
    message = _find_param(body, "message")
    # message is optional; medterm4ds emits it always — verify shape.
    if message is not None:
        assert "valueString" in message, (
            f"message parameter missing valueString: {message!r}"
        )
        assert isinstance(message["valueString"], str), (
            f"message.valueString is not a string: {type(message['valueString'])}"
        )


# ===========================================================================
# Lens 4: Optional parameters — ``targetSystem``, ``targetCode``,
# ``sourceScope``, ``targetScope``, ``reverse``, ``ConceptMap`` (URL),
# ``coding``, ``codeableConcept``. (Item 2 of chunk scope.)
# ===========================================================================


def test_s40_targetsystem_present_constrains_results(fhir_client):
    """SKEPTIC (item 2): when ``targetSystem`` is supplied, the response
    MUST constrain matches to the specified target system. No matches
    from other systems should appear.

    Spec: FHIR R4 $translate In Parameters ``targetSystem`` (0..1 uri).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    for match in matches:
        concept_part = next(
            (p for p in match.get("part", []) if p.get("name") == "concept"),
            None,
        )
        coding = concept_part["valueCoding"]
        assert coding["system"] == ICD10CM_URI, (
            f"match.concept.system drift when targetSystem={ICD10CM_URI!r}: "
            f"got {coding['system']!r}. The targetSystem constraint MUST "
            f"limit results to the requested target system."
        )


def test_s41_targetsystem_absent_returns_all_known_targets(fhir_client):
    """SKEPTIC (item 2): when ``targetSystem`` is ABSENT, the server
    should return matches across all known target systems (the spec
    says "if targetSystem not provided, then the server may use any
    available map").

    The current implementation calls ``_all_systems_except(source)``
    (CR-008/CR-020 carry-forward notes the hardcoded list).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    body = r.json()
    # The fixture only seeds ONE cross-system mapping (SNOMED↔ICD-10-CM
    # via CUI C0011847). The 'no targetSystem' path SHOULD still find
    # that mapping. So at least 1 match is expected.
    result = _find_param(body, "result")
    assert result is not None and result.get("valueBoolean") is True, (
        f"no-targetSystem path should still find the SNOMED→ICD-10-CM "
        f"mapping; got result={result}."
    )


def test_s42_post_coding_only_body_now_honored_via_helper(fhir_client):
    """SKEPTIC (item 2 — coding alternative encoding): POST $translate
    with a ``coding`` parameter (per FHIR R4 In Parameters) instead of
    system+code. CF-CM02-01 LANDED via CM-01 EXPLORER QA-001 —
    ``_extract_named_coding_from_parameters`` is now wired into
    ``_extract_translate_params`` AND ``translate_post``.

    The 7th instance of cross-handler-helper-wiring inconsistency
    (count=6 PROMOTED as of CS-04 HISTORIAN QA-052). Same pattern as
    TS-02 HISTORIAN QA-022 on CodeSystem/$lookup.

    Updated from prior 400-expecting shape when CF-CM02-01 was deferred.
    Carry-forward-as-probe pattern (CS-03 TERMINOLOGIST methodology) —
    methodology fired loudly on the CM-01 EXPLORER fix as designed.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {
                        "system": SNOMED_URI,
                        "code": "44054006",
                    },
                },
                {
                    "name": "targetsystem",
                    "valueUri": ICD10CM_URI,
                },
            ],
        },
    )
    # CF-CM02-01 RESOLVED: helper is wired; coding body produces 200.
    assert r.status_code == 200, (
        f"POST $translate with coding-only body — CF-CM02-01 RESOLVED "
        f"requires 200 (coding now honored). Got {r.status_code}: {r.text}"
    )
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"Content-Type drift: {r.headers['content-type']!r}; "
        f"expected application/fhir+json."
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters", (
        f"resourceType drift: {body.get('resourceType')!r}; "
        f"expected Parameters."
    )


def test_s43_post_codeableconcept_body_now_honored_via_helper(fhir_client):
    """SKEPTIC (item 2 — codeableConcept alternative encoding): POST
    $translate with a ``codeableConcept`` parameter. CF-CM02-01 LANDED
    via CM-01 EXPLORER QA-001 — ``_extract_codeable_concept_from_parameters``
    is now wired into ``_extract_translate_params``.

    Per FHIR R4 In Parameters, ``codeableConcept`` is 0..1 CodeableConcept.
    Updated from prior 400-expecting shape when CF-CM02-01 was deferred.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
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
                {
                    "name": "targetsystem",
                    "valueUri": ICD10CM_URI,
                },
            ],
        },
    )
    # CF-CM02-01 RESOLVED: codeableConcept extractor now wired.
    assert r.status_code == 200, (
        f"POST $translate with codeableConcept body — CF-CM02-01 RESOLVED "
        f"requires 200 (codeableConcept now honored). Got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"


# ===========================================================================
# Lens 5: ConceptMap URL constraint — when the client supplies a
# ConceptMap URL via the ``url`` parameter, the server should consult
# only that map. (Item 6 of chunk scope.)
# ===========================================================================


def test_s50_conceptmap_url_param_accepted_current_behavior(fhir_client):
    """SKEPTIC (item 6): GET $translate with the ``url`` parameter
    (canonical ConceptMap URL per FHIR R4 In Parameters).

    The current ``translate_get`` handler signature does NOT declare
    ``url``. FastAPI passes unrecognized query params through silently —
    the URL is not used to select a named ConceptMap. Same shape as the
    ``source`` param declared at line 1974 (description: "Passed through;
    not yet used to select a named ConceptMap").

    The probe verifies the request does NOT 500 (spec-compatibility
    fallback) — the operation proceeds as if no URL had been supplied.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("url", CONCEPTMAP_URL),
        ],
    )
    # The url param is accepted (FastAPI does not reject unknown query
    # params on a handler). The handler ignores it and proceeds.
    assert r.status_code == 200, (
        f"GET $translate with url param — expected 200 (url is ignored); "
        f"got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"


def test_s51_conceptmap_url_param_ignored_results_same_as_without(fhir_client):
    """SKEPTIC (item 6, sibling of test_s50): when the ``url`` parameter
    is supplied, the medterm4ds implementation returns the SAME results
    as without it (because the URL is not used to select a map).

    A spec-conformant server MIGHT consult only the named ConceptMap,
    which could return DIFFERENT (potentially fewer) results. The
    medterm4ds implementation accepts the param for spec-compatibility
    but does not implement the constraint. Filed as DEFERRED — wiring
    the URL constraint requires ConceptMap persistence (out of v0.0.x
    scope per AGENTS.md "Search params always return empty Bundle").
    """
    r_with = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("url", CONCEPTMAP_URL),
        ],
    )
    r_without = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r_with.status_code == r_without.status_code == 200
    # The implementation ignores the url param; results are identical.
    body_with = r_with.json()
    body_without = r_without.json()
    matches_with = [p for p in body_with.get("parameter", []) if p.get("name") == "match"]
    matches_without = [p for p in body_without.get("parameter", []) if p.get("name") == "match"]
    assert len(matches_with) == len(matches_without), (
        f"Expected identical results with/without url param (implementation "
        f"ignores it); got {len(matches_with)} vs {len(matches_without)}."
    )


# ===========================================================================
# Lens 6: targetScope constraint — when specified, only matches in that
# scope returned. (Item 7 of chunk scope.)
# ===========================================================================


def test_s60_targetscope_param_accepted_current_behavior(fhir_client):
    """SKEPTIC (item 7): GET $translate with the ``targetScope``
    parameter (ValueSet URL per FHIR R4 In Parameters).

    The current handler does NOT declare ``targetScope``. FastAPI passes
    the unknown query param through silently. The probe verifies the
    request does NOT 500.

    A spec-conformant server would constrain matches to those in the
    specified targetScope (a ValueSet URL). medterm4ds does not implement
    the constraint. Filed as DEFERRED — wiring requires ValueSet
    persistence (out of v0.0.x scope).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("targetScope", "http://example.org/fhir/ValueSet/test-diabetes"),
        ],
    )
    assert r.status_code == 200, (
        f"GET $translate with targetScope — expected 200 (param accepted "
        f"but ignored); got {r.status_code}: {r.text}"
    )


def test_s61_sourcescope_param_accepted_current_behavior(fhir_client):
    """SKEPTIC (item 2 — sourceScope, sibling of test_s60): GET
    $translate with the ``sourceScope`` parameter (ValueSet URL per FHIR
    R4 In Parameters).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("sourceScope", "http://example.org/fhir/ValueSet/test-snomed"),
        ],
    )
    assert r.status_code == 200, (
        f"GET $translate with sourceScope — expected 200 (param accepted "
        f"but ignored); got {r.status_code}: {r.text}"
    )


def test_s62_reverse_param_accepted_current_behavior(fhir_client):
    """SKEPTIC (item 2 — reverse): GET $translate with ``reverse=true``.

    Per AGENTS.md NOT A BUG registry: "$translate?reverse=true accepted
    but not fully implemented". The handler accepts the param via the
    GET signature (declared only on the ``targetCode`` description at
    line 1980). The reverse-mode logic is not wired.

    The probe verifies the request does NOT 500 — spec-compatibility
    fallback.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", ICD10CM_URI),
            ("code", "E11"),
            ("targetsystem", SNOMED_URI),
            ("reverse", "true"),
        ],
    )
    # Current behavior: reverse param is silently dropped; the handler
    # does a forward translation. The request should succeed.
    assert r.status_code == 200, (
        f"GET $translate with reverse=true — expected 200 (reverse mode "
        f"silently dropped per AGENTS.md NOT A BUG registry); got "
        f"{r.status_code}: {r.text}"
    )


def test_s63_targetprune_param_accepted_current_behavior(fhir_client):
    """SKEPTIC (item 2 — targetPrune): GET $translate with
    ``targetPrune=true``.

    NOTE: per FHIR R4 spec, ``targetPrune`` is NOT a documented In
    Parameter (it was added in R5). The probe documents that medterm4ds
    accepts the param without error (spec-compatibility fallback) and
    ignores it. This is conformant — the spec does not mandate rejection
    of unknown parameters.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("targetPrune", "true"),
        ],
    )
    # targetPrune is silently dropped (not a R4 param). The handler
    # should proceed normally.
    assert r.status_code == 200, (
        f"GET $translate with targetPrune=true — expected 200 (param "
        f"silently dropped, not a R4 param); got {r.status_code}: {r.text}"
    )


def test_s64_version_param_accepted_current_behavior(fhir_client):
    """SKEPTIC (item 2 — version): GET $translate with the ``version``
    parameter (per FHIR R4 In Parameters, 0..1 string).

    Same shape as the version param on $lookup / $validate-code /
    $subsumes (documented in AGENTS.md NOT A BUG registry as "accepted
    but ignored"). The handler signature does NOT declare ``version``,
    but FastAPI passes unknown query params through.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("version", "2024-09"),
        ],
    )
    assert r.status_code == 200, (
        f"GET $translate with version — expected 200 (param accepted but "
        f"ignored, single-snapshot engine); got {r.status_code}: {r.text}"
    )


# ===========================================================================
# Lens 7: Equivalence vocabulary audit — every emitted value from R4 enum.
# Direct audit of the canonical equivalence module (post-CR-024).
# ===========================================================================


def test_s70_canonical_module_emits_only_r4_values():
    """SKEPTIC (vocabulary audit): the canonical equivalence module
    (post-CR-024) MUST emit only R4 ConceptMapEquivalence values.

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    """
    emitted = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
    drift = emitted - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert not drift, (
        f"INTERNAL_REL_TO_FHIR_EQUIVALENCE emits values outside R4 enum: {drift}."
    )


def test_s71_canonical_module_resolves_all_engine_vocab():
    """SKEPTIC (vocabulary audit): every engine-vocabulary token from
    ``core/models.py:conceptmap_relationship`` MUST resolve to an R4
    value via the canonical module.

    The engine emits (verified by source reading): ``equivalent``,
    ``source-is-narrower-than-target``, ``source-is-broader-than-target``,
    ``related-to``, ``not-translated``, ``unmatched``. Per
    CR-024/milestone-3 review, the canonical module MUST resolve all 6.
    """
    engine_vocab = {
        "equivalent",
        "source-is-narrower-than-target",
        "source-is-broader-than-target",
        "related-to",
        "not-translated",
        "unmatched",
    }
    for token in engine_vocab:
        resolved = fhir_equivalence(token)
        assert resolved in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"engine vocab {token!r} resolves to non-R4 value {resolved!r}"
        )


def test_s72_canonical_module_directionality_per_r4_spec():
    """SKEPTIC (vocabulary audit, CR-024 + CM01-SKEPTIC-001 carry):
    R4 ``narrower`` / ``wider`` are read from TARGET perspective. The
    canonical module MUST map engine vocabulary correctly:

      * ``source-is-narrower-than-target`` → target is WIDER → R4 ``wider``
      * ``source-is-broader-than-target`` → target is NARROWER → R4 ``narrower``

    Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
    Found by CM-01 SKEPTIC (CM01-SKEPTIC-001). The milestone-3
    remediation landed the canonical module with the directionality fix.
    """
    assert (
        INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-narrower-than-target"] == "wider"
    ), (
        "CM01-SKEPTIC-001 regression: source-is-narrower-than-target must "
        "map to R4 'wider' (target is wider than source)."
    )
    assert (
        INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-broader-than-target"] == "narrower"
    ), (
        "CM01-SKEPTIC-001 regression: source-is-broader-than-target must "
        "map to R4 'narrower' (target is narrower than source)."
    )


def test_s73_fhir_equivalence_helper_uses_canonical_module():
    """SKEPTIC (CR-024 audit): the ``fhir_equivalence`` helper in
    ``outputs/fhir.py`` and the ``_fhir_equivalence_from_relationship``
    wrapper in ``responses.py`` MUST both delegate to the canonical
    module (post-CR-024 consolidation).

    Found by milestone-3 review (CR-024). The two prior parallel maps
    translated the same engine vocabulary with divergent key/value
    pairs. The canonical module eliminated the drift.
    """
    # Direct test: both helpers resolve the same input to the same value.
    for token in (
        "equivalent",
        "source-is-narrower-than-target",
        "source-is-broader-than-target",
        "related-to",
        "not-translated",
        "unmatched",
        "subsumedby",  # CF-HISTORIAN-VS01-01 — must resolve to R4 specializes
    ):
        v1 = fhir_equivalence(token)
        v2 = _fhir_equivalence_from_relationship(token)
        assert v1 == v2, (
            f"helper drift on input {token!r}: fhir_equivalence()={v1!r}, "
            f"_fhir_equivalence_from_relationship()={v2!r}. CR-024 "
            f"consolidation MUST eliminate cross-helper drift."
        )


# ===========================================================================
# Lens 8: build_parameters_translate builder audit — direct unit-level
# test of the builder.
# ===========================================================================


def test_s80_build_parameters_translate_empty_mappings_result_false():
    """SKEPTIC (builder audit): when ``mappings=[]``, the builder MUST
    emit ``result=false``, NO match entries, and a conformant message.

    Spec: Out Parameters.
    """
    body = build_parameters_translate(
        mappings=[],
        source_system_uri=SNOMED_URI,
        source_code="44054006",
    )
    assert body["resourceType"] == "Parameters"
    result = _find_param(body, "result")
    assert result is not None and result["valueBoolean"] is False
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    assert len(matches) == 0


def test_s81_build_parameters_translate_message_is_string():
    """SKEPTIC (builder audit): the ``message`` parameter MUST always
    be a string (even when there are zero matches).

    Spec: Out ``message`` is 0..1 string.
    """
    body = build_parameters_translate(
        mappings=[],
        source_system_uri=SNOMED_URI,
        source_code="44054006",
    )
    message = _find_param(body, "message")
    assert message is not None
    assert "valueString" in message
    assert isinstance(message["valueString"], str)


def test_s82_build_parameters_translate_match_shape():
    """SKEPTIC (builder audit): when given a mapping, the builder MUST
    emit a match entry with equivalence, concept (Coding), and source
    (Coding). Each Coding has system + code.
    """
    from medterm4ds.core.models import CodeMapping, CodeRef

    mapping = CodeMapping(
        source=CodeRef(source="SNOMEDCT_US", code="44054006"),
        target=CodeRef(source="ICD10CM", code="E11"),
        source_display="Type 2 diabetes mellitus",
        target_display="Type 2 diabetes mellitus",
        relationship="equivalent",
        match_type="exact",
    )
    body = build_parameters_translate(
        mappings=[mapping],
        source_system_uri=SNOMED_URI,
        source_code="44054006",
    )
    result = _find_param(body, "result")
    assert result is not None and result["valueBoolean"] is True
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    assert len(matches) == 1
    match = matches[0]
    part_names = {p.get("name") for p in match.get("part", [])}
    assert "equivalence" in part_names
    assert "concept" in part_names
    assert "source" in part_names


# ===========================================================================
# Lens 9: Canonical-URI echo — verify CR-012 still holds with multiple
# input variants on the Out match.source.system field.
# ===========================================================================


@pytest.mark.parametrize(
    "alias,expected_canonical",
    [
        (SNOMED_URI, SNOMED_URI),  # canonical → canonical
        (SNOMED_URI_OID_ALIAS, SNOMED_URI),  # OID alias → canonical
        (SNOMED_URI_TRAILING_SLASH, SNOMED_URI),  # trailing slash → canonical
    ],
    ids=["canonical", "urn-oid-alias", "trailing-slash"],
)
def test_s90_translate_canonical_source_system_uri(fhir_client, alias, expected_canonical):
    """SKEPTIC (CR-012 RESOLVED — parametrized): for every alias input,
    the Out ``match.source.system`` field MUST be the canonical URI.

    Spec: FHIR R4 Out Parameters. CR-012 wrapped ``source_uri`` through
    ``canonical_system_uri`` before passing to the builder.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", alias),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200, f"alias={alias!r}; got {r.status_code}: {r.text}"
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(matches) > 0
    for match in matches:
        source_part = next(
            (p for p in match.get("part", []) if p.get("name") == "source"),
            None,
        )
        coding = source_part["valueCoding"]
        assert coding["system"] == expected_canonical, (
            f"alias={alias!r} → match.source.system drift: got "
            f"{coding['system']!r}; expected canonical {expected_canonical!r}."
        )


# ===========================================================================
# Lens 10: ConceptMap instance-level operation route — verify the
# instance-level $translate route exists per FHIR R4 §3.1.0.1.1.
# ===========================================================================


def test_s100_instance_level_translate_route_exists(fhir_client):
    """SKEPTIC (chunk-description-as-probe): the instance-level
    ConceptMap $translate route (``/fhir/ConceptMap/{id}/$translate``)
    SHOULD be registered per FHIR R4 §3.1.0.1.1.

    The type-level route is at ``/fhir/ConceptMap/$translate``. The
    instance-level route is needed for spec compliance — FHIR R4 permits
    operations on either type or instance. medterm4ds does not persist
    ConceptMaps, so the instance-level route is informational only
    (returns the same result as the type-level route or a 404
    OperationOutcome).

    The probe verifies the route exists and returns a FHIR-conformant
    response (not a Starlette default 404 with text/plain body).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/any-id/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    # The route SHOULD exist (either 200 with Parameters, or 404/405 with
    # OperationOutcome). The probe fails if Starlette returns its default
    # 404 with text/plain body (non-FHIR).
    assert r.status_code != 404 or r.headers["content-type"].startswith(
        "application/fhir+json"
    ), (
        f"instance-level $translate route appears to be missing — "
        f"Starlette default 404 with non-FHIR body. Status: "
        f"{r.status_code}; Content-Type: {r.headers.get('content-type')!r}."
    )


# ===========================================================================
# Lens 11: Cross-handler audit — translate_get vs translate_post MUST
# produce identical clinical content for the same logical inputs.
# ===========================================================================


def test_s110_get_post_parity_on_translate(fhir_client):
    """SKEPTIC (cross-handler parity): GET and POST $translate with the
    same logical inputs (system+code+targetsystem) MUST produce
    identical clinical content (same match count, same equivalence
    values, same target codes).

    Spec: FHIR R4 operations are invocable via GET or POST with
    equivalent semantics.

    Mirrors CS-05 EXPLORER cross-operation-canonical-agreement pattern.
    """
    r_get = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
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
    assert r_get.status_code == 200
    assert r_post.status_code == 200

    body_get = r_get.json()
    body_post = r_post.json()

    def _summary(body: dict[str, Any]) -> tuple[int, list[tuple[str, str, str]]]:
        result = _find_param(body, "result")
        result_val = result["valueBoolean"] if result else None
        matches_summary = []
        for p in body.get("parameter", []):
            if p.get("name") != "match":
                continue
            equiv = next(
                (part.get("valueCode") for part in p.get("part", []) if part.get("name") == "equivalence"),
                None,
            )
            concept = next(
                (part.get("valueCoding") for part in p.get("part", []) if part.get("name") == "concept"),
                None,
            )
            matches_summary.append(
                (equiv, concept.get("system", "") if concept else "", concept.get("code", "") if concept else "")
            )
        return (result_val, sorted(matches_summary))

    get_summary = _summary(body_get)
    post_summary = _summary(body_post)
    assert get_summary == post_summary, (
        f"GET↔POST clinical content drift on $translate:\n"
        f"  GET : {get_summary}\n"
        f"  POST: {post_summary}"
    )
