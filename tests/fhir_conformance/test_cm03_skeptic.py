"""SKEPTIC probes for chunk CM-03 (ConceptMap $closure Operation).

Source: https://build.fhir.org/conceptmap-operation-closure.html
Canonical R4 OperationDefinition: https://hl7.org/fhir/R4/conceptmap-operation-closure.html

Chunk scope (7 items):
  1. Required params: ``name`` (closure name).
  2. Optional params: ``concept`` (repeating valueCoding).
  3. When no ``concept`` param: initialize/reset the closure, return
     version hash.
  4. When ``concept`` params: add concepts to closure, return version
     hash + parameter list.
  5. Version hash changes when closure state changes.
  6. Closure enables fast ``$subsumes`` via pre-computed relationship
     table.
  7. Subsumption within closure returns correct outcome
     (``subsumes``/``subsumed-by``/``equivalent``/``not-subsumed``).

SKEPTIC lens (adversarial bug hunting):
  * Required params: drop ``name`` — expect 400.
  * Closure initialization (POST name only — no concepts): initialize
    or reset; version hash returned.
  * Add concepts (POST name + 1 concept / multiple concepts).
  * Version hash semantics: same state → same hash; different state →
    different hash; hash format.
  * Subsumption within closure: parent + child added →
    ClosureTable.check returns the correct outcome.
  * ``incomplete_since`` flag (B6 fix): when the closure table is
    incomplete, ``$subsumes`` answers may be degraded.
  * Edge cases: closure name collisions, re-initialization, add
    concepts with unknown system.
  * SPEC DEVIATION: per FHIR R4
    https://hl7.org/fhir/R4/conceptmap-operation-closure.html the
    Out ``return`` parameter is a ConceptMap (1..1) — the medterm4ds
    implementation emits a Parameters resource with ``return`` as
    valueString + repeating ``concept`` valueCoding. This SKEPTIC
    iteration probes whether the current shape is internally
    consistent AND documents the deviation (the spec-correct shape
    requires reworking the entire response surface, which is out of
    scope for a single iteration).

Note: medterm4ds ``$closure`` is batched (E1 fix per commit history).
``ClosureTable.add_concepts`` walks ancestors+descendants per source
(2 walks per source, not 2 per concept). The closure table is
server-side and ``$subsumes`` does NOT consult it (the handler walks
the hierarchy directly via ``is_descendant``). The closure is
exercised via ``ClosureTable.check`` (Python API) and the
``closure-add-concepts`` cases.json custom_check.
"""

from __future__ import annotations

from typing import Any

import pytest

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
# A URI that the FHIR URI map does not know.
UNKNOWN_SYSTEM_URI = "http://example.org/unknown-system"


def _closure_param_name_only(name: str) -> dict[str, Any]:
    return {
        "resourceType": "Parameters",
        "parameter": [{"name": "name", "valueString": name}],
    }


def _closure_param_with_concepts(
    name: str, concepts: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "name", "valueString": name},
            *[
                {"name": "concept", "valueCoding": c}
                for c in concepts
            ],
        ],
    }


def _find_param(body: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Return the first ``parameter`` entry with ``name == name``, else None."""
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
# Lens 1: Required params — drop ``name`` — expect 400. (Item 1.)
# ===========================================================================


def test_s10_post_closure_missing_name_returns_400(fhir_client):
    """SKEPTIC (item 1): POST ``$closure`` WITHOUT ``name`` MUST return 400.

    Spec: FHIR R4 In Parameters lists ``name`` as 1..1 string. The
    implementation at ``closure_post`` (apps/fhir_api.py) explicitly
    checks for ``name`` and returns 400 with a clear message.

    Adversarial: drop ``name`` entirely. Expect 400 + OperationOutcome
    + conformant Content-Type.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json={"resourceType": "Parameters", "parameter": []},
    )
    assert r.status_code == 400, (
        f"POST $closure without name — expected 400; got "
        f"{r.status_code}: {r.text}"
    )
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"Content-Type drift on error path: {r.headers['content-type']!r}"
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"resourceType drift on error path: {body.get('resourceType')!r}"
    )


def test_s11_post_closure_empty_name_value_returns_400(fhir_client):
    """SKEPTIC (item 1): POST ``$closure`` with empty-string ``name``
    MUST return 400.

    Adversarial: ``name=""`` is "present" but empty. The
    implementation uses ``if not name`` which catches both None and
    empty string. Verify the empty-string case is also rejected
    (otherwise a client bug producing empty-string names would
    silently create a closure named "" — collision-prone).
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json={
            "resourceType": "Parameters",
            "parameter": [{"name": "name", "valueString": ""}],
        },
    )
    assert r.status_code == 400, (
        f"POST $closure with empty name — expected 400; got "
        f"{r.status_code}: {r.text}"
    )


def test_s12_post_closure_name_only_value_code_not_value_string(fhir_client):
    """SKEPTIC (item 1): ``name`` sent as ``valueCode`` instead of
    ``valueString`` is silently dropped by ``_parse_parameters`` (which
    extracts valueString/valueUri/valueCode/valueInteger/valueBoolean
    — valueCode IS recognized — but let's confirm the explicit check
    on valueString).

    Wait — valueCode IS in the scalar extractor list. So ``name`` as
    valueCode SHOULD be extracted. This test confirms that path.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "name", "valueCode": "alt-encoding"}
            ],
        },
    )
    # valueCode is extracted by _parse_parameters — should succeed.
    assert r.status_code == 200, (
        f"POST $closure with name as valueCode — expected 200 (valueCode "
        f"is in the scalar extractor list); got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"


def test_s13_post_closure_name_in_query_string_ignored(fhir_client):
    """SKEPTIC (item 1): ``name`` sent ONLY as a query string parameter
    is silently ignored — the implementation reads body params only.

    Adversarial: FHIR R4 operations may be invoked via GET OR POST on
    either the type or a resource instance per §3.1.0.1.1. The
    medterm4ds ``$closure`` POST handler reads body params only;
    query string ``?name=foo`` is silently ignored. Probe documents
    current behavior.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure?name=via-query",
        json={"resourceType": "Parameters", "parameter": []},
    )
    # Body has no name → 400.
    assert r.status_code == 400, (
        f"POST $closure with name ONLY in query string — expected 400 "
        f"(handler reads body only); got {r.status_code}: {r.text}"
    )


# ===========================================================================
# Lens 2: Closure initialization — POST ``name`` only → reset, version
# hash returned. (Item 3.)
# ===========================================================================


def test_s20_post_closure_init_returns_parameters_resource(fhir_client):
    """SKEPTIC (item 3): POST ``$closure`` with name only returns a
    Parameters resource.

    Implementation: ``build_closure_response`` returns
    ``resourceType: Parameters`` with ``return`` (valueString) +
    optional ``concept`` entries.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only("skeptic-init-20"),
    )
    assert r.status_code == 200, (
        f"expected 200; got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters", (
        f"resourceType drift: {body.get('resourceType')!r}"
    )


def test_s21_post_closure_init_has_return_value_string(fhir_client):
    """SKEPTIC (item 3): the response MUST include a ``return`` parameter
    with a non-empty ``valueString`` (version hash).

    The version hash is the client's signal of closure state. Without
    it, the client cannot detect state changes across calls.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only("skeptic-init-21"),
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}"
    body = r.json()
    ret = _find_param(body, "return")
    assert ret is not None, (
        "$closure response missing 'return' parameter."
    )
    assert "valueString" in ret, (
        f"'return' parameter missing valueString key: {ret!r}"
    )
    assert ret["valueString"], (
        f"'return' valueString is empty: {ret!r}"
    )


def test_s22_post_closure_init_version_hash_format(fhir_client):
    """SKEPTIC (item 5): version hash is an MD5-hex prefix (12 chars).

    Implementation: ``ClosureTable.version_hash`` returns
    ``hashlib.md5(...).hexdigest()[:12]``. Probe documents the
    format — a future change that drops the [:12] or switches
    algorithm should update this probe.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only("skeptic-init-22"),
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}"
    body = r.json()
    h = _return_hash(body)
    assert h is not None and len(h) == 12, (
        f"version hash format drift: got {h!r} (len {len(h) if h else 'None'}, expected 12)"
    )
    assert all(c in "0123456789abcdef" for c in h), (
        f"version hash is not lowercase hex: {h!r}"
    )


def test_s23_post_closure_init_idempotent_same_hash(fhir_client):
    """SKEPTIC (item 5): calling init twice on the same name produces
    the same version hash (same closure state).

    Implementation: each call to ``manager.reset(name)`` creates a
    FRESH ClosureTable with ``_version=0`` and empty concepts. The
    hash is derived from ``len(concepts):_version:sorted(concepts.keys())``
    → "0:0:[]". Two consecutive resets yield the same hash.
    """
    r1 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only("skeptic-init-23"),
    )
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only("skeptic-init-23"),
    )
    assert r1.status_code == 200 and r2.status_code == 200
    h1 = _return_hash(r1.json())
    h2 = _return_hash(r2.json())
    assert h1 == h2, (
        f"init twice should produce same hash (idempotent); got "
        f"{h1!r} then {h2!r}"
    )


# ===========================================================================
# Lens 3: Add concepts — POST name + concept(s) → state changes.
# (Items 2 + 4.)
# ===========================================================================


def test_s30_post_closure_add_single_concept_changes_hash(fhir_client):
    """SKEPTIC (items 2 + 5): adding a concept to an existing closure
    MUST change the version hash.

    Implementation: ``ClosureTable.add_concepts`` increments
    ``_version`` and adds to ``concepts``. The hash incorporates both.
    """
    # Initialize
    r0 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only("skeptic-add-30"),
    )
    assert r0.status_code == 200
    h0 = _return_hash(r0.json())

    # Add one concept
    r1 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            "skeptic-add-30",
            [{"system": SNOMED_URI, "code": "73211009", "display": "Diabetes mellitus"}],
        ),
    )
    assert r1.status_code == 200, f"add concept — got {r1.status_code}: {r1.text}"
    h1 = _return_hash(r1.json())
    assert h1 != h0, (
        f"adding a concept MUST change the version hash; both were {h0!r}"
    )


def test_s31_post_closure_add_concepts_response_includes_concept_list(fhir_client):
    """SKEPTIC (item 4): the response after add includes the parameter
    list (the concept entries now in the closure).

    Implementation: ``build_closure_response`` includes
    ``closure.to_parameter_list()`` which emits one ``concept``
    parameter per concept in the closure.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            "skeptic-add-31",
            [
                {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
                {"system": SNOMED_URI, "code": "44054006", "display": "T2DM"},
            ],
        ),
    )
    assert r.status_code == 200, f"got {r.status_code}: {r.text}"
    body = r.json()
    concepts = _find_params(body, "concept")
    assert len(concepts) >= 2, (
        f"response should include the added concepts; got {len(concepts)} entries"
    )
    codes = {c["valueCoding"]["code"] for c in concepts}
    assert "73211009" in codes and "44054006" in codes, (
        f"response concept list missing added codes; got {codes}"
    )


def test_s32_post_closure_add_concept_unknown_system_accepted(fhir_client):
    """SKEPTIC (item 2 + edge): adding a concept whose ``system`` URI
    is NOT in the FHIR URI map is accepted (the implementation falls
    back to using the URI verbatim as the source).

    Adversarial: probe whether unknown systems silently produce
    broken closure entries. The implementation at ``_do_closure``
    uses ``fhir_uri_to_system(system_uri) or system_uri`` — so the
    raw URI becomes the "source" key. The hierarchy walks then
    no-op because ``get_ancestors``/``get_descendants`` query by
    source label; the raw URI matches nothing.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            "skeptic-unknown-32",
            [{"system": UNKNOWN_SYSTEM_URI, "code": "X1", "display": "Unknown"}],
        ),
    )
    # The implementation accepts unknown systems — no validation gate.
    assert r.status_code == 200, (
        f"adding unknown-system concept — expected 200 (no validation gate); "
        f"got {r.status_code}: {r.text}"
    )
    body = r.json()
    # The concept IS added to the closure (with the raw URI as system).
    concepts = _find_params(body, "concept")
    assert len(concepts) >= 1, (
        f"unknown-system concept should still appear in closure response"
    )


def test_s33_post_closure_add_concept_oid_alias_system_translated(fhir_client):
    """SKEPTIC (item 2 + canonical URI): adding a concept whose
    ``system`` is an OID alias (``urn:oid:2.16.840.1.113883.6.96``)
    — the implementation should translate it via
    ``fhir_uri_to_system`` to the internal source label.

    If the alias is NOT in ``FHIR_URI_ALIASES``, the raw OID becomes
    the source key and hierarchy walks no-op (same as test_s32).
    Probe documents current behavior.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            "skeptic-oid-33",
            [{"system": SNOMED_URI_OID_ALIAS, "code": "73211009", "display": "DM"}],
        ),
    )
    assert r.status_code == 200, (
        f"got {r.status_code}: {r.text}"
    )
    # The response echoes back the canonical SNOMED URI (because
    # to_parameter_list uses system_to_fhir_uri when the source is
    # recognized) OR the raw OID (when not recognized).
    body = r.json()
    concepts = _find_params(body, "concept")
    # At minimum, the concept is present in the response.
    assert len(concepts) >= 1


# ===========================================================================
# Lens 4: Version hash semantics — same/different state. (Item 5.)
# ===========================================================================


def test_s40_version_hash_incorporates_concept_count(fhir_client):
    """SKEPTIC (item 5): the version hash incorporates the number of
    concepts in the closure. Adding more concepts MUST change the hash
    even if the version counter happens to match.
    """
    name = "skeptic-hash-40"
    r1 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [
                {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
                {"system": SNOMED_URI, "code": "44054006", "display": "T2DM"},
            ],
        ),
    )
    h1 = _return_hash(r1.json())
    h2 = _return_hash(r2.json())
    assert h1 != h2, (
        f"adding a second concept MUST change hash (count differs); "
        f"got {h1!r} then {h2!r}"
    )


def test_s41_version_hash_independent_of_concept_order(fhir_client):
    """SKEPTIC (item 5): the version hash is independent of the order
    in which concepts are added (because ``version_hash`` uses
    ``sorted(self.concepts.keys())``).

    Adversarial: add A then B to closure X; add B then A to closure Y.
    The resulting state should produce the same hash IF and only IF
    the internal _version counter matches. Since add_concepts always
    increments by 1 per call, both closures will have _version=1
    after one call → same hash.

    Note: this probe documents that adding the same concept set via
    one batched call produces a deterministic hash regardless of
    client-supplied order.
    """
    name_x = "skeptic-order-X-41"
    name_y = "skeptic-order-Y-41"
    r_x = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name_x,
            [
                {"system": SNOMED_URI, "code": "44054006", "display": "T2DM"},
                {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
            ],
        ),
    )
    r_y = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name_y,
            [
                {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
                {"system": SNOMED_URI, "code": "44054006", "display": "T2DM"},
            ],
        ),
    )
    h_x = _return_hash(r_x.json())
    h_y = _return_hash(r_y.json())
    assert h_x == h_y, (
        f"hash should be order-independent (sorted keys); "
        f"got X={h_x!r}, Y={h_y!r}"
    )


def test_s42_version_hash_changes_on_reinit(fhir_client):
    """SKEPTIC (item 5): re-initializing a closure that had concepts
    resets state. The version hash returns to the empty-state value
    (which is DIFFERENT from the populated-state hash).
    """
    name = "skeptic-reinit-42"
    # Add concepts
    r_add = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    h_after_add = _return_hash(r_add.json())
    # Re-init
    r_reinit = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only(name),
    )
    h_after_reinit = _return_hash(r_reinit.json())
    assert h_after_add != h_after_reinit, (
        f"re-init MUST change the hash (state was different); "
        f"both were {h_after_add!r}"
    )


# ===========================================================================
# Lens 5: Closure enables fast ``$subsumes`` via pre-computed table.
# (Item 6 + 7.)
# ===========================================================================


def test_s50_closure_check_after_add_concepts_returns_subsumes(fhir_client):
    """SKEPTIC (items 6 + 7): after adding parent (Diabetes 73211009)
    and child (T2DM 44054006) to the closure,
    ``ClosureTable.check(parent, child)`` returns "subsumes".

    The fixture seeds mrrel row ``A44054006 isa A73211009`` (T2DM is-a
    Diabetes). The closure's batched ancestor walk discovers that
    Diabetes is an ancestor of T2DM; both are in the closure, so
    the relationship is recorded.
    """
    name = "skeptic-subsumes-50"
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [
                {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
                {"system": SNOMED_URI, "code": "44054006", "display": "T2DM"},
            ],
        ),
    )
    assert r.status_code == 200, f"got {r.status_code}: {r.text}"
    closure = get_closure_manager().get(name)
    assert closure is not None, "closure not found after add"
    assert closure.check("73211009", "44054006") == "subsumes", (
        f"check(parent, child) should be 'subsumes'; got "
        f"{closure.check('73211009', '44054006')!r}"
    )


def test_s51_closure_check_after_add_concepts_returns_subsumed_by(fhir_client):
    """SKEPTIC (item 7): mirror of test_s50 —
    ``ClosureTable.check(child, parent)`` returns "subsumed-by".
    """
    name = "skeptic-subsumes-51"
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [
                {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
                {"system": SNOMED_URI, "code": "44054006", "display": "T2DM"},
            ],
        ),
    )
    closure = get_closure_manager().get(name)
    assert closure is not None
    assert closure.check("44054006", "73211009") == "subsumed-by", (
        f"check(child, parent) should be 'subsumed-by'; got "
        f"{closure.check('44054006', '73211009')!r}"
    )


def test_s52_closure_check_self_returns_equivalent(fhir_client):
    """SKEPTIC (item 7): ``ClosureTable.check(code, code)`` returns
    "equivalent" (self-subsumption).
    """
    name = "skeptic-equiv-52"
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    closure = get_closure_manager().get(name)
    assert closure is not None
    assert closure.check("73211009", "73211009") == "equivalent", (
        f"check(code, code) should be 'equivalent'; got "
        f"{closure.check('73211009', '73211009')!r}"
    )


def test_s53_closure_check_unrelated_returns_not_subsumed(fhir_client):
    """SKEPTIC (item 7): two concepts added to the closure that are
    NOT in a subsumption relationship return "not-subsumed".

    Adversarial: SNOMED 73211009 (DM) and RXNORM 860975 (metformin)
    are from different systems and have no hierarchical relation.
    The batched walk per source finds no shared ancestors/descendants.
    """
    name = "skeptic-unrelated-53"
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [
                {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
                {"system": RXNORM_URI, "code": "860975", "display": "metformin"},
            ],
        ),
    )
    closure = get_closure_manager().get(name)
    assert closure is not None
    assert closure.check("73211009", "860975") == "not-subsumed", (
        f"check(unrelated) should be 'not-subsumed'; got "
        f"{closure.check('73211009', '860975')!r}"
    )


def test_s54_closure_check_codes_not_in_closure_returns_not_subsumed(fhir_client):
    """SKEPTIC (item 7 + edge): ``ClosureTable.check(A, B)`` for codes
    NOT in the closure returns "not-subsumed" (not an error).
    """
    name = "skeptic-notin-54"
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    closure = get_closure_manager().get(name)
    assert closure is not None
    # 44054006 was NOT added to this closure.
    assert closure.check("73211009", "44054006") == "not-subsumed", (
        f"check(code-in-closure, code-not-in-closure) should be 'not-subsumed'; "
        f"got {closure.check('73211009', '44054006')!r}"
    )


# ===========================================================================
# Lens 6: ``$subsumes`` does NOT use the closure table — confirm.
# (Item 6 — the "fast" path is currently unused on the HTTP surface.)
# ===========================================================================


def test_s60_subsumes_does_not_consult_closure_table(fhir_client):
    """SKEPTIC (item 6): the ``$subsumes`` HTTP handler walks the
    hierarchy directly (via ``is_descendant``) and does NOT consult
    ``ClosureTable.check``. The closure table is built but the
    subsumption operation does not use it.

    Adversarial: confirm that ``$subsumes`` returns the correct
    outcome even when the closure table is empty (no $closure call
    has been made). If ``$subsumes`` consulted the closure, this
    would return "not-subsumed" — but it returns "subsumes" because
    it walks the hierarchy directly.

    Spec context: FHIR R4 $closure enables CLIENT-SIDE fast subsumption
    via the returned ConceptMap. The medterm4ds implementation keeps
    the table server-side for potential future use; ``$subsumes``
    walks the hierarchy directly today. Probe documents current
    behavior.
    """
    # DO NOT initialize any closure table — leave it empty.
    r = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params=[
            ("system", SNOMED_URI),
            ("codeA", "73211009"),
            ("codeB", "44054006"),
        ],
    )
    assert r.status_code == 200, f"got {r.status_code}: {r.text}"
    body = r.json()
    outcome = _find_param(body, "outcome")
    assert outcome is not None
    # Outcome is "subsumes" (A=Diabetes subsumes B=T2DM).
    assert outcome.get("valueCode") == "subsumes", (
        f"$subsumes should return 'subsumes' via direct hierarchy walk; "
        f"got {outcome.get('valueCode')!r}"
    )


# ===========================================================================
# Lens 7: ``incomplete_since`` flag (B6 fix per GLOBAL_RULES.md).
# ===========================================================================


def test_s70_closure_incomplete_since_starts_false():
    """SKEPTIC: ``ClosureTable.incomplete_since`` starts False.

    Per GLOBAL_RULES.md "Silent Fallbacks" + B6 fix: the flag is set
    True only after a transient DuckDB failure during an ancestor/
    descendant walk. A fresh closure has no failures yet.
    """
    t = ClosureTable("test-s70")
    assert t.incomplete_since is False, (
        "fresh ClosureTable.incomplete_since should be False"
    )


def test_s71_closure_incomplete_since_set_on_duckdb_error():
    """SKEPTIC: when ``get_ancestors`` raises ``duckdb.Error``, the
    closure is marked incomplete (``incomplete_since = True``) and
    the WARNING is logged.

    Per GLOBAL_RULES.md B6 fix: ``add_concept`` catches ``duckdb.Error``
    at WARNING level and sets ``incomplete_since``. The conformance
    fixture cannot inject a duckdb.Error via the HTTP surface (the
    test client uses a working DB), so this probe monkey-patches
    both ``get_ancestors`` AND ``get_descendants`` (the single-concept
    path calls both sequentially).
    """
    import duckdb as _duckdb
    from medterm4ds.engines.fhir import closure as closure_mod

    class _NullEngine:
        pass

    original_anc = closure_mod.get_ancestors
    original_desc = closure_mod.get_descendants
    closure_mod.get_ancestors = lambda *a, **k: (_ for _ in ()).throw(
        _duckdb.Error("synthetic ancestor failure")
    )
    closure_mod.get_descendants = lambda *a, **k: (_ for _ in ()).throw(
        _duckdb.Error("synthetic descendant failure")
    )
    try:
        t = ClosureTable("test-s71")
        t.add_concept("X1", "SNOMEDCT_US", "X1 display", _NullEngine())
        assert t.incomplete_since is True, (
            "incomplete_since should be True after duckdb.Error in ancestor walk"
        )
    finally:
        closure_mod.get_ancestors = original_anc
        closure_mod.get_descendants = original_desc


def test_s72_closure_add_concepts_incomplete_since_set_on_duckdb_error():
    """SKEPTIC: same as test_s71 but on the batched ``add_concepts``
    path (E1 fix). The batched path catches ``duckdb.Error`` per
    source per direction and sets ``incomplete_since``.
    """
    import duckdb as _duckdb
    from medterm4ds.engines.fhir import closure as closure_mod

    class _BoomEngine:
        pass

    original_anc = closure_mod.get_ancestors
    original_desc = closure_mod.get_descendants
    closure_mod.get_ancestors = lambda *a, **k: (_ for _ in ()).throw(
        _duckdb.Error("synthetic ancestor batch failure")
    )
    closure_mod.get_descendants = lambda *a, **k: (_ for _ in ()).throw(
        _duckdb.Error("synthetic descendant batch failure")
    )
    try:
        t = ClosureTable("test-s72")
        t.add_concepts(
            [("X1", "SNOMEDCT_US", "X1"), ("X2", "SNOMEDCT_US", "X2")],
            _BoomEngine(),
        )
        assert t.incomplete_since is True, (
            "incomplete_since should be True after batched duckdb.Error"
        )
    finally:
        closure_mod.get_ancestors = original_anc
        closure_mod.get_descendants = original_desc


# ===========================================================================
# Lens 8: Closure name collisions / re-initialization (edge cases).
# ===========================================================================


def test_s80_post_closure_reinit_clears_concepts(fhir_client):
    """SKEPTIC: re-initializing a closure (POST name only after adding
    concepts) clears the concept list.

    Implementation: ``manager.reset(name)`` creates a fresh
    ClosureTable, discarding the prior one. The next response shows
    0 concept entries.
    """
    name = "skeptic-reinit-80"
    # Add concepts
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [
                {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
                {"system": SNOMED_URI, "code": "44054006", "display": "T2DM"},
            ],
        ),
    )
    # Re-init
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only(name),
    )
    assert r.status_code == 200
    body = r.json()
    concepts = _find_params(body, "concept")
    assert len(concepts) == 0, (
        f"re-init should clear concepts; got {len(concepts)} entries"
    )


def test_s81_closure_isolation_between_names(fhir_client):
    """SKEPTIC: two closure names are isolated — adding to one does
    not affect the other.
    """
    name_a = "skeptic-iso-A-81"
    name_b = "skeptic-iso-B-81"
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name_a,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    # name_b is initialized empty (no add)
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only(name_b),
    )
    manager = get_closure_manager()
    a = manager.get(name_a)
    b = manager.get(name_b)
    assert a is not None and b is not None
    assert len(a.concepts) >= 1
    assert len(b.concepts) == 0, (
        f"closure {name_b} should be empty (isolated from {name_a})"
    )


def test_s82_closure_add_to_existing_preserves_prior(fhir_client):
    """SKEPTIC: adding concepts to an existing closure preserves the
    prior concepts (add is cumulative, not replace).
    """
    name = "skeptic-cumulative-82"
    # Add one
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    # Add another
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "44054006", "display": "T2DM"}],
        ),
    )
    body = r2.json()
    concepts = _find_params(body, "concept")
    codes = {c["valueCoding"]["code"] for c in concepts}
    assert "73211009" in codes and "44054006" in codes, (
        f"both concepts should be in closure after cumulative add; got {codes}"
    )


def test_s83_closure_name_with_special_chars(fhir_client):
    """SKEPTIC: closure name with special characters (dash, underscore,
    dot, digits) is accepted and the closure can be retrieved.
    """
    name = "skeptic-special-name.v2_test-83"
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only(name),
    )
    assert r.status_code == 200, f"got {r.status_code}: {r.text}"
    closure = get_closure_manager().get(name)
    assert closure is not None, (
        f"closure {name!r} not retrievable after creation"
    )


# ===========================================================================
# Lens 9: SPEC DEVIATION — Out ``return`` should be ConceptMap per R4.
# ===========================================================================


def test_s90_spec_deviation_return_is_value_string_not_conceptmap(fhir_client):
    """SKEPTIC (SPEC DEVIATION): the current implementation emits
    ``return`` as a ``valueString`` (the version hash) and repeating
    ``concept`` valueCoding parameters inside a Parameters resource.

    Per FHIR R4
    https://hl7.org/fhir/R4/conceptmap-operation-closure.html Out
    Parameters: ``return`` is 1..1 ConceptMap (NOT string). The
    spec-correct shape is a Parameters resource with a single
    ``return`` parameter whose ``resource`` field is a ConceptMap
    containing the closure table as group.element.target entries.

    This probe documents the current shape (carry-forward-as-probe
    pattern — asserts CURRENT behavior). When the spec-correct shape
    is implemented, this probe MUST be updated to assert the new
    shape.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only("skeptic-spec-90"),
    )
    assert r.status_code == 200
    body = r.json()
    ret = _find_param(body, "return")
    assert ret is not None
    # Current shape: valueString (the version hash).
    assert "valueString" in ret, (
        f"current shape — 'return' should have valueString; got {ret!r}"
    )
    # Spec-correct shape would have 'resource' of type ConceptMap —
    # NOT present today.
    assert "resource" not in ret, (
        "if 'return' has a 'resource' field, the spec-correct shape "
        "has been implemented — update this probe."
    )


def test_s91_spec_deviation_no_conceptmap_resource_in_response(fhir_client):
    """SKEPTIC (SPEC DEVIATION, mirror of test_s90): the response
    contains NO ConceptMap resource anywhere. Per R4 spec, the
    ``return`` Out parameter is a ConceptMap.

    Probe verifies the absence (current) — when the spec-correct
    shape lands, this probe MUST be tightened.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_with_concepts(
            "skeptic-spec-91",
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    assert r.status_code == 200
    body = r.json()
    # Walk the response looking for any resourceType=ConceptMap.
    found_conceptmap = False
    if isinstance(body, dict):
        if body.get("resourceType") == "ConceptMap":
            found_conceptmap = True
        for p in body.get("parameter", []):
            if isinstance(p, dict) and isinstance(p.get("resource"), dict):
                if p["resource"].get("resourceType") == "ConceptMap":
                    found_conceptmap = True
    assert not found_conceptmap, (
        "response contains a ConceptMap resource — spec-correct shape "
        "implemented; update this probe to assert presence+shape."
    )


# ===========================================================================
# Lens 10: Content-Type / wire-format on the $closure route.
# ===========================================================================


def test_s100_post_closure_success_content_type(fhir_client):
    """SKEPTIC: POST ``$closure`` success path emits
    ``application/fhir+json`` Content-Type (not the framework default
    ``application/json``).

    Per FHIR R4 §3.1.0.1.9 + AGENTS.md "operation handlers MUST funnel
    through _fhir_response".
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only("skeptic-ct-100"),
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"Content-Type drift on success path: {r.headers['content-type']!r}"
    )


def test_s101_post_closure_error_content_type(fhir_client):
    """SKEPTIC: POST ``$closure`` error path (missing name) emits
    ``application/fhir+json`` Content-Type.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json={"resourceType": "Parameters", "parameter": []},
    )
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"Content-Type drift on error path: {r.headers['content-type']!r}"
    )


def test_s102_post_closure_xml_format_accepted(fhir_client):
    """SKEPTIC: POST ``$closure`` with ``_format=xml`` returns XML
    serialization (CR-002 fix — _scalar_to_xml_attr applies to all
    serializers).
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_param_name_only("skeptic-xml-102"),
        params={"_format": "xml"},
    )
    assert r.status_code == 200, f"got {r.status_code}: {r.text}"
    assert r.headers["content-type"].startswith("application/fhir+xml"), (
        f"_format=xml should produce application/fhir+xml; got {r.headers['content-type']!r}"
    )
    body_text = r.text
    assert "<Parameters" in body_text or "<parameters" in body_text.lower(), (
        f"XML body should contain <Parameters>; got: {body_text[:200]!r}"
    )


# ===========================================================================
# Lens 11: Manager-level invariants — singleton, get_or_create.
# ===========================================================================


def test_s110_get_closure_manager_singleton():
    """SKEPTIC: ``get_closure_manager`` returns the same instance
    across calls (singleton guarded by lock).
    """
    m1 = get_closure_manager()
    m2 = get_closure_manager()
    assert m1 is m2, (
        "get_closure_manager should return the same singleton instance"
    )


def test_s111_get_or_create_idempotent_for_existing_name():
    """SKEPTIC: ``manager.get_or_create(name)`` on an existing name
    returns the existing table (not a new one).
    """
    manager = ClosureManager()
    t1 = manager.get_or_create("test-goc-111")
    t1.add_concept  # ensure it's a ClosureTable
    t2 = manager.get_or_create("test-goc-111")
    assert t1 is t2, (
        "get_or_create on existing name should return same instance"
    )


def test_s112_reset_creates_fresh_table():
    """SKEPTIC: ``manager.reset(name)`` always creates a new table,
    discarding prior state.
    """
    manager = ClosureManager()
    t1 = manager.get_or_create("test-reset-112")
    t1.concepts["X"] = {"system": "S", "display": "X"}
    t2 = manager.reset("test-reset-112")
    assert t1 is not t2, "reset should return a new instance"
    assert len(t2.concepts) == 0, "fresh table should be empty"


def test_s113_get_returns_none_for_unknown_name():
    """SKEPTIC: ``manager.get(name)`` returns None for unknown names.
    """
    manager = ClosureManager()
    assert manager.get("nonexistent-s113") is None


# ===========================================================================
# Lens 12: ``build_closure_response`` direct unit test.
# ===========================================================================


def test_s120_build_closure_response_includes_return_and_concepts():
    """SKEPTIC: ``build_closure_response`` produces a Parameters
    resource with ``return`` first, then ``concept`` entries.
    """
    t = ClosureTable("test-bcr-120")
    t.concepts["73211009"] = {"system": "SNOMEDCT_US", "display": "DM"}
    t.concepts["44054006"] = {"system": "SNOMEDCT_US", "display": "T2DM"}
    params = build_closure_response(t)
    assert params["resourceType"] == "Parameters"
    names = [p["name"] for p in params["parameter"]]
    assert names[0] == "return", (
        f"first parameter should be 'return'; got {names!r}"
    )
    # Concepts are sorted by code: 44054006 < 73211009
    concept_idx = names.index("concept")
    assert names[concept_idx] == "concept"
    # Both concepts present
    concept_codes = [
        p["valueCoding"]["code"]
        for p in params["parameter"]
        if p.get("name") == "concept"
    ]
    assert sorted(concept_codes) == ["44054006", "73211009"]


def test_s121_build_closure_response_empty_closure_has_only_return():
    """SKEPTIC: ``build_closure_response`` on an empty closure produces
    a Parameters resource with ONLY the ``return`` parameter.
    """
    t = ClosureTable("test-bcr-121")
    params = build_closure_response(t)
    assert params["resourceType"] == "Parameters"
    names = [p["name"] for p in params["parameter"]]
    assert names == ["return"], (
        f"empty closure should have only 'return'; got {names!r}"
    )


# ===========================================================================
# Lens 13: Batch dispatcher — ``$closure`` in a Bundle entry.
# ===========================================================================


def test_s130_batch_closure_init_via_bundle(fhir_client):
    """SKEPTIC: POST ``/fhir`` Bundle with a ``$closure`` entry works
    via the batch dispatcher (added per TS-04 HISTORIAN QA-039).
    """
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "/CodeSystem/$closure",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "name", "valueString": "skeptic-batch-130"}
                        ],
                    },
                }
            ],
        },
    )
    assert r.status_code == 200, f"got {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("resourceType") == "Bundle"
    assert body.get("type") == "batch-response"
    assert len(body.get("entry", [])) == 1
    entry = body["entry"][0]
    assert entry["response"]["status"] == "200"
    assert entry["resource"]["resourceType"] == "Parameters"


def test_s131_batch_closure_missing_name_returns_400_entry(fhir_client):
    """SKEPTIC: ``$closure`` batch entry WITHOUT name returns per-entry
    400 (per-entry error isolation, not whole-batch failure).
    """
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                {
                    "request": {
                        "method": "POST",
                        "url": "/CodeSystem/$closure",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [],
                    },
                }
            ],
        },
    )
    assert r.status_code == 200, f"got {r.status_code}: {r.text}"
    body = r.json()
    entry = body["entry"][0]
    assert entry["response"]["status"] == "400", (
        f"per-entry status should be 400; got {entry['response']['status']!r}"
    )
    assert entry["resource"]["resourceType"] == "OperationOutcome"
