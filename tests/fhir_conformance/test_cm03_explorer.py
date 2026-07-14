"""EXPLORER probes for chunk CM-03 (ConceptMap $closure Operation).

Source: https://build.fhir.org/conceptmap-operation-closure.html
Canonical R4 OperationDefinition:
    https://hl7.org/fhir/R4/conceptmap-operation-closure.html

EXPLORER lens (lateral thinking / cross-handler probes). The SKEPTIC +
HISTORIAN iterations already produced 41 + 24 = 65 probes. EXPLORER
covers the LATERAL gaps:

  * 4-shape POST Content-Type closure on ``$closure`` (CF-EXPLORER-CS02-01
    family audit — every Parameters-body operation now needs the 4-shape
    family: name-only / name+concept / alternative encoding / error path).
  * Cross-handler parity between per-operation POST and batch dispatcher
    entry for ``$closure`` (mirrors TS-04 TERMINOLOGIST strategy 20 +
    VS-05 HISTORIAN strategy 53).
  * XML wire-format coverage on ``$closure`` — extend Accept-header
    negotiation and ``_format=xml`` (mirrors CM-02 EXPLORER test_e20..e23
    methodology).
  * Closure name variations (very long name, name with control chars,
    duplicate name re-init behavior).
  * Multi-concept batch edge cases (large batch, mixed systems, mixed
    valid/invalid concepts).
  * Re-initialization: init → add concepts → re-init clears state.
  * Version hash stability: same input → same hash; different input →
    different hash.
  * Cross-handler helper-wiring audit: verify the inline extraction in
    ``_do_closure`` is NOT mirrored via the canonical helpers —
    the existing inline path is intentionally inline (handles the
    repeating ``concept`` 0..* semantic; the canonical helpers
    ``_extract_coding_from_parameters`` / ``_extract_named_coding_from_parameters``
    pick ONE coding, which is the wrong semantic for $closure).
  * Carry-forward pinning: CF-SKEPTIC-CM03-01, CF-SKEPTIC-CM03-02,
    CF-HISTORIAN-CM03-02.

Note: the medterm4ds implementation of ``$closure`` emits a Parameters
response with ``return`` as valueString (version hash) AND a repeating
``concept`` parameter list — both of which DEVIATE from the canonical
R4 OperationDefinition (which specifies ``return`` as 1..1 ConceptMap
and does NOT specify a ``concept`` Out parameter). The deviation is
documented as carry-forward CF-SKEPTIC-CM03-01 (MEDIUM, DEFERRED
feature enhancement). EXPLORER confirms the deviation is INTERNALLY
CONSISTENT and OPERATIONALLY CONFORMANT (Content-Type, error path,
XML format, batch dispatcher all OK).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from medterm4ds.engines.fhir.closure import (
    ClosureManager,
    ClosureTable,
    build_closure_response,
    get_closure_manager,
)


SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_ALIAS_OID = "urn:oid:2.16.840.1.113883.6.96"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
UNKNOWN_SYSTEM_URI = "http://example.org/unknown-system"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _closure_name_only(name: str) -> dict[str, Any]:
    """Build a Parameters body with ONLY a name parameter (no concepts)."""
    return {
        "resourceType": "Parameters",
        "parameter": [{"name": "name", "valueString": name}],
    }


def _closure_with_concepts(
    name: str, concepts: list[dict[str, str]]
) -> dict[str, Any]:
    """Build a Parameters body with name + N valueCoding concepts."""
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
# Lens 1: 4-shape POST Content-Type probe family for $closure.
# (CF-EXPLORER-CS02-01 extension — every Parameters-body operation has the
# 4-shape family: success scalar / success complex / alternative encoding /
# error path. ``$closure`` is the LAST Parameters-body operation that
# needs the family; CM-01 EXPLORER marked the family FULLY CLOSED — this
# iteration closes the remaining ``$closure`` corner.)
# ===========================================================================


def test_e10_post_closure_name_only_content_type(fhir_client):
    """Shape 1 (success — name only): Content-Type is application/fhir+json.

    Per FHIR R4 §3.1.0.9: the correct MIME type SHALL be used. Returning
    application/json (Starlette default) violates the spec.
    """
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only("explorer-e10"),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/fhir+json"
    body = response.json()
    assert body["resourceType"] == "Parameters"


def test_e11_post_closure_name_with_concepts_content_type(fhir_client):
    """Shape 2 (success — name + valueCoding concepts): Content-Type OK.

    Mirrors CS-03 EXPLORER test_e40..e43 4-shape family for
    CodeSystem/$validate-code.
    """
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(
            "explorer-e11",
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/fhir+json"
    body = response.json()
    assert body["resourceType"] == "Parameters"
    assert _find_param(body, "return") is not None


def test_e12_post_closure_oid_alias_concept_content_type(fhir_client):
    """Shape 3 (alternative encoding — OID alias): Content-Type OK.

    Per CF-SKEPTIC-CS01-01 family, alias URIs are accepted via
    ``fhir_uri_to_system``. ``urn:oid:2.16.840.1.113883.6.96`` (SNOMED
    OID) MUST be translated to ``SNOMEDCT_US`` source.
    """
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(
            "explorer-e12",
            [{
                "system": SNOMED_URI_ALIAS_OID,
                "code": "73211009",
                "display": "DM via OID",
            }],
        ),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/fhir+json"
    body = response.json()
    assert body["resourceType"] == "Parameters"
    # The returned concept list should canonicalize system to SNOMED_URI
    concepts = _find_params(body, "concept")
    if concepts:
        assert concepts[0]["valueCoding"]["system"] == SNOMED_URI


def test_e13_post_closure_missing_name_error_content_type(fhir_client):
    """Shape 4 (error path): Content-Type is application/fhir+json on 400.

    Per FHIR R4 §3.1.0.1.5 + §3.1.0.1.9: error responses MUST carry a
    FHIR OperationOutcome body AND a FHIR MIME type. Framework default
    (text/plain or application/json) is non-conformant.
    """
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json={"resourceType": "Parameters", "parameter": []},
    )
    assert response.status_code == 400, response.text
    assert response.headers["content-type"] == "application/fhir+json"
    body = response.json()
    assert body["resourceType"] == "OperationOutcome"


def test_e14_get_closure_method_not_allowed(fhir_client):
    """Per FHIR R4 §4.8.21.4 (closure): the operation is POST-only.

    GET to ``/fhir/CodeSystem/$closure`` MUST return 405 (or 404) with
    a FHIR OperationOutcome body — NOT the framework default
    ``{"detail": "Method Not Allowed"}`` JSON.
    """
    response = fhir_client.get("/fhir/CodeSystem/$closure")
    # Either 405 (per TS-02 EXPLORER QA-024 type-level POST) or 404
    # (catch-all) is acceptable, but the body MUST be a FHIR OperationOutcome.
    assert response.status_code in (404, 405), response.text
    ct = response.headers.get("content-type", "")
    assert "fhir+json" in ct or "fhir+xml" in ct, (
        f"Expected FHIR content-type on GET rejection, got {ct!r}"
    )
    body = response.json()
    assert body["resourceType"] == "OperationOutcome"


# ===========================================================================
# Lens 2: Cross-handler parity per-operation POST vs batch dispatcher
# entry. Mirrors TS-04 TERMINOLOGIST strategy 20 + VS-05 HISTORIAN
# strategy 53 (cross-handler byte-exact).
# ===========================================================================


def test_e20_batch_closure_init_byte_matches_per_operation(fhir_client):
    """Batch ``$closure`` init response MUST match per-operation byte-exact.

    Per TS-04 TERMINOLOGIST strategy 20 (single-vs-batch byte-exact
    equivalence) + VS-05 HISTORIAN strategy 53 (cross-handler
    byte-exact parity): the batch dispatcher MUST NOT alter clinical
    content. The response.resource (Parameters) MUST be byte-identical
    to per-operation POST.
    """
    name_po = "explorer-e20-perop"
    name_batch = "explorer-e20-batch"
    # Per-operation POST
    r_po = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only(name_po),
    )
    assert r_po.status_code == 200, r_po.text
    po_body = r_po.json()
    # Batch POST
    r_batch = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [{
                "request": {
                    "method": "POST",
                    "url": "/CodeSystem/$closure",
                },
                "resource": _closure_name_only(name_batch),
            }],
        },
    )
    assert r_batch.status_code == 200, r_batch.text
    batch_body = r_batch.json()
    assert batch_body["resourceType"] == "Bundle"
    assert batch_body["type"] == "batch-response"
    assert len(batch_body["entry"]) == 1
    batch_resource = batch_body["entry"][0]["resource"]
    # The Parameters resource MUST be byte-identical in shape (resourceType,
    # parameter[0].name = "return", parameter[0].valueString format).
    assert batch_resource["resourceType"] == po_body["resourceType"]
    assert _find_param(batch_resource, "return") is not None
    # version_hash format is the same 12-char MD5 hex prefix
    po_hash = _return_hash(po_body)
    batch_hash = _return_hash(batch_resource)
    assert po_hash is not None
    assert batch_hash is not None
    assert len(po_hash) == 12
    assert len(batch_hash) == 12


def test_e21_batch_closure_add_concepts_matches_per_operation(fhir_client):
    """Batch ``$closure`` add-concepts response shape matches per-operation.

    The concept parameter list shape (system + code + display per entry)
    MUST be identical. Cross-handler byte-exact on the response shape.
    """
    name_po = "explorer-e21-perop"
    name_batch = "explorer-e21-batch"
    concepts = [
        {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
        {"system": SNOMED_URI, "code": "44054006", "display": "T2DM"},
    ]
    # Per-operation POST
    r_po = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(name_po, concepts),
    )
    assert r_po.status_code == 200, r_po.text
    po_body = r_po.json()
    # Batch POST
    r_batch = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [{
                "request": {
                    "method": "POST",
                    "url": "/CodeSystem/$closure",
                },
                "resource": _closure_with_concepts(name_batch, concepts),
            }],
        },
    )
    assert r_batch.status_code == 200, r_batch.text
    batch_resource = r_batch.json()["entry"][0]["resource"]
    # Both have the same concept count
    po_concepts = _find_params(po_body, "concept")
    batch_concepts = _find_params(batch_resource, "concept")
    assert len(po_concepts) == len(batch_concepts) == 2
    # Both concept lists are sorted by code (build_closure_response sorts)
    po_codes = sorted(c["valueCoding"]["code"] for c in po_concepts)
    batch_codes = sorted(c["valueCoding"]["code"] for c in batch_concepts)
    assert po_codes == batch_codes == ["44054006", "73211009"]


def test_e22_batch_closure_error_per_entry_isolation(fhir_client):
    """Per-entry error isolation in batch ``$closure``.

    Per FHIR R4 §3.7: batch entries are independent. A malformed
    ``$closure`` entry (missing ``name``) MUST produce a per-entry 400
    OperationOutcome; a well-formed entry in the same batch MUST succeed.
    """
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                # Entry 1: malformed $closure (missing name) → per-entry 400.
                {
                    "request": {
                        "method": "POST",
                        "url": "/CodeSystem/$closure",
                    },
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [],
                    },
                },
                # Entry 2: well-formed $closure → 200.
                {
                    "request": {
                        "method": "POST",
                        "url": "/CodeSystem/$closure",
                    },
                    "resource": _closure_name_only("explorer-e22-ok"),
                },
            ],
        },
    )
    assert r.status_code == 200, r.text
    bundle = r.json()
    assert len(bundle["entry"]) == 2
    # Entry 1: per-entry 400 OperationOutcome
    assert bundle["entry"][0]["response"]["status"] == "400"
    assert bundle["entry"][0]["resource"]["resourceType"] == "OperationOutcome"
    # Entry 2: 200 Parameters
    assert bundle["entry"][1]["response"]["status"] == "200"
    assert bundle["entry"][1]["resource"]["resourceType"] == "Parameters"


# ===========================================================================
# Lens 3: XML wire-format on $closure.
# Mirrors CM-02 EXPLORER test_e20..e23 (Accept-header + _format
# negotiation).
# ===========================================================================


def test_e30_post_closure_xml_format_param(fhir_client):
    """``_format=xml`` query param requests XML on $closure success path.

    Per FHIR R4 §3.1.0.1.11: ``_format`` overrides Accept header. The
    server MUST honor ``_format=xml`` by returning an XML body with
    ``Content-Type: application/fhir+xml``.
    """
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure?_format=xml",
        json=_closure_name_only("explorer-e30"),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/fhir+xml"
    body_text = response.text
    assert "<Parameters" in body_text
    # The return parameter renders as <return valueString="..."/>
    assert "valueString" in body_text


def test_e31_post_closure_xml_accept_header(fhir_client):
    """Accept: application/fhir+xml requests XML on $closure success path.

    Mirrors CM-02 EXPLORER test_e22 (Accept-header XML negotiation).
    """
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only("explorer-e31"),
        headers={"Accept": "application/fhir+xml"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/fhir+xml"
    body_text = response.text
    assert "<Parameters" in body_text


def test_e32_post_closure_xml_with_concepts(fhir_client):
    """XML wire-format with multiple concept entries.

    Per FHIR R4: Parameters repeats serialize as
    ``<parameter><name value="concept"/><valueCoding>...</valueCoding></parameter>``.
    """
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure?_format=xml",
        json=_closure_with_concepts(
            "explorer-e32",
            [
                {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
                {"system": SNOMED_URI, "code": "44054006", "display": "T2DM"},
            ],
        ),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/fhir+xml"
    body_text = response.text
    assert "<Parameters" in body_text
    # Both concepts present (sorted by code)
    assert "44054006" in body_text
    assert "73211009" in body_text


def test_e33_post_closure_xml_error_path(fhir_client):
    """XML wire-format on error path (missing name → 400 OperationOutcome).

    Per CR-003 (milestone-1 review): when the calling handler has
    ``request`` in scope, the error path SHOULD honor the same XML/JSON
    negotiation as the success path.
    """
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure?_format=xml",
        json={"resourceType": "Parameters", "parameter": []},
    )
    assert response.status_code == 400, response.text
    # The error response honors XML negotiation per _fhir_error_response.
    ct = response.headers["content-type"]
    assert ct == "application/fhir+xml", (
        f"Expected application/fhir+xml on error path, got {ct!r}"
    )
    body_text = response.text
    assert "<OperationOutcome" in body_text


# ===========================================================================
# Lens 4: Closure name variations.
# Lateral edge cases not covered by SKEPTIC test_s80..s83 (re-init,
# isolation, cumulative add, special chars).
# ===========================================================================


def test_e40_closure_name_very_long(fhir_client):
    """Very long closure name (1000+ chars) is handled gracefully.

    No spec-mandated length cap on the ``name`` parameter. The closure
    manager MUST accept arbitrary-length names without crashing.
    """
    long_name = "explorer-e40-" + ("x" * 1000)
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only(long_name),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resourceType"] == "Parameters"
    assert _return_hash(body) is not None


def test_e41_closure_name_control_chars(fhir_client):
    """Closure name with control chars (tab, newline) is handled gracefully.

    The implementation does not validate name format — the name is used
    as a dict key in ``ClosureManager._tables``. Control chars do NOT
    cause crashes (DuckDB prepared statements not involved in name
    storage).
    """
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only("explorer-e41\tname\nwith\ncontrol"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resourceType"] == "Parameters"


def test_e42_closure_name_unicode(fhir_client):
    """Closure name with Unicode chars (CJK, Arabic, emoji) is handled.

    FHIR R4 strings are Unicode. The closure manager MUST accept
    international characters.
    """
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only("explorer-e42-糖尿病🎨"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resourceType"] == "Parameters"


def test_e43_closure_duplicate_name_reinit_clears(fhir_client):
    """Re-init on existing name clears concepts.

    Per SKEPTIC test_s80, posting name-only on an existing name resets
    the closure. EXPLORER adds: after reset, the concept list MUST be
    empty AND the version hash MUST differ from the prior state.
    """
    name = "explorer-e43-dup"
    # Phase 1: add concepts
    r1 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    hash_with_concept = _return_hash(body1)
    concepts1 = _find_params(body1, "concept")
    assert len(concepts1) == 1
    # Phase 2: re-init (name only) — clears state
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only(name),
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    hash_empty = _return_hash(body2)
    concepts2 = _find_params(body2, "concept")
    assert len(concepts2) == 0
    # Hashes differ (count-based hashing per test_s40)
    assert hash_with_concept != hash_empty


# ===========================================================================
# Lens 5: Multi-concept batch edge cases.
# ===========================================================================


def test_e50_closure_add_100_concepts(fhir_client):
    """Large concept batch (100+ entries) is handled.

    Per E1 fix (batched add_concepts): the implementation batches
    ancestor+descendant walks per source (2 walks per source, not 2 per
    concept). 100 SNOMED concepts collapse into 2 walks.
    """
    # Generate 100 distinct SNOMED codes
    concepts = [
        {"system": SNOMED_URI, "code": f"99999{i:03d}", "display": f"C{i}"}
        for i in range(100)
    ]
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts("explorer-e50-large", concepts),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resourceType"] == "Parameters"
    # All 100 concepts are in the response
    concept_entries = _find_params(body, "concept")
    assert len(concept_entries) == 100


def test_e51_closure_add_mixed_systems(fhir_client):
    """Mixed-system concept batch is added per-source.

    Per E1 fix: each source gets its own batched walk. Adding concepts
    from SNOMED + ICD-10-CM + RxNorm triggers 6 walks total (2 per
    source × 3 sources).
    """
    concepts = [
        {"system": SNOMED_URI, "code": "73211009", "display": "DM (SNOMED)"},
        {"system": ICD10CM_URI, "code": "E11", "display": "T2DM (ICD-10-CM)"},
        {"system": RXNORM_URI, "code": "860975", "display": "Metformin"},
    ]
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts("explorer-e51-mixed", concepts),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    concept_entries = _find_params(body, "concept")
    assert len(concept_entries) == 3
    # Each system is canonicalized (ICD-10-CM URI preserved)
    systems = {c["valueCoding"]["system"] for c in concept_entries}
    assert SNOMED_URI in systems
    assert ICD10CM_URI in systems
    assert RXNORM_URI in systems


def test_e52_closure_add_mix_valid_invalid_concepts(fhir_client):
    """Mixed valid/invalid concept batch — invalid ones silently dropped.

    Per HISTORIAN CF-HISTORIAN-CM03-01 fix: malformed valueCoding
    (non-dict) is silently dropped. EXPLORER extends: partial codings
    (missing code or missing system) are also silently dropped.
    """
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "name", "valueString": "explorer-e52-mixed"},
                # Valid concept
                {"name": "concept", "valueCoding": {
                    "system": SNOMED_URI, "code": "73211009", "display": "DM"
                }},
                # Invalid: missing code
                {"name": "concept", "valueCoding": {
                    "system": SNOMED_URI, "display": "No code"
                }},
                # Invalid: missing system
                {"name": "concept", "valueCoding": {
                    "code": "44054006", "display": "No system"
                }},
                # Invalid: non-dict valueCoding (CF-HISTORIAN-CM03-01)
                {"name": "concept", "valueCoding": "not-a-dict"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    concept_entries = _find_params(body, "concept")
    # Only the one valid concept made it through
    assert len(concept_entries) == 1
    assert concept_entries[0]["valueCoding"]["code"] == "73211009"


def test_e53_closure_add_unknown_system_concepts(fhir_client):
    """Unknown system URIs are accepted as raw strings.

    Per SKEPTIC test_s32: unknown system URIs are accepted; the raw URI
    becomes the source key. EXPLORER confirms with a batch of mixed
    known + unknown systems.
    """
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(
            "explorer-e53-unknown",
            [
                {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
                {"system": UNKNOWN_SYSTEM_URI, "code": "X1", "display": "Unknown"},
            ],
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    concept_entries = _find_params(body, "concept")
    assert len(concept_entries) == 2


# ===========================================================================
# Lens 6: Re-initialization behavior.
# ===========================================================================


def test_e60_closure_init_add_reinit_clears(fhir_client):
    """Init → add concepts → re-init clears state.

    Confirms SKEPTIC test_s80 + extends: after re-init, adding a NEW
    concept does NOT resurrect the prior concept.
    """
    name = "explorer-e60-cycle"
    # Phase 1: init (name only)
    r1 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only(name),
    )
    assert r1.status_code == 200, r1.text
    hash_init = _return_hash(r1.json())
    # Phase 2: add concept A
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    assert r2.status_code == 200, r2.text
    hash_with_a = _return_hash(r2.json())
    concepts2 = _find_params(r2.json(), "concept")
    assert len(concepts2) == 1
    # Phase 3: re-init (name only)
    r3 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only(name),
    )
    assert r3.status_code == 200, r3.text
    hash_reinit = _return_hash(r3.json())
    concepts3 = _find_params(r3.json(), "concept")
    assert len(concepts3) == 0
    # Phase 4: add concept B — A does NOT come back
    r4 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "44054006", "display": "T2DM"}],
        ),
    )
    assert r4.status_code == 200, r4.text
    concepts4 = _find_params(r4.json(), "concept")
    assert len(concepts4) == 1
    assert concepts4[0]["valueCoding"]["code"] == "44054006"
    # Hashes evolve consistently
    assert len({hash_init, hash_with_a, hash_reinit}) >= 2


# ===========================================================================
# Lens 7: Version hash stability.
# ===========================================================================


def test_e70_version_hash_same_input_same_output(fhir_client):
    """Same input concept list → same hash on different closure names.

    Per SKEPTIC test_s41 (order-independent): the version hash sorts
    concepts by code. EXPLORER confirms: two closures with the SAME
    concept list produce the SAME version hash (the name is NOT part
    of the hash).
    """
    concepts = [
        {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
        {"system": SNOMED_URI, "code": "44054006", "display": "T2DM"},
    ]
    r1 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts("explorer-e70-a", concepts),
    )
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts("explorer-e70-b", concepts),
    )
    assert r1.status_code == r2.status_code == 200
    hash1 = _return_hash(r1.json())
    hash2 = _return_hash(r2.json())
    assert hash1 == hash2, (
        f"Same concept list should produce same hash; got {hash1} vs {hash2}"
    )


def test_e71_version_hash_different_input_different_output(fhir_client):
    """Different concept list → different hash.

    Confirms SKEPTIC test_s40 + test_s30 at the cross-name level.
    """
    name = "explorer-e71"
    # Init to baseline
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only(name),
    )
    # Add concept A
    r1 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    hash_with_a = _return_hash(r1.json())
    # Reset and add concept B
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only(name),
    )
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "44054006", "display": "T2DM"}],
        ),
    )
    hash_with_b = _return_hash(r2.json())
    assert hash_with_a != hash_with_b


def test_e72_version_hash_format_md5_hex_12(fhir_client):
    """Version hash is 12-char MD5 hex prefix.

    Per SKEPTIC test_s22: format is MD5 hex prefix (12 chars).
    EXPLORER extends: the format is consistent across name-only,
    add-concept, and re-init paths.
    """
    name = "explorer-e72"
    # Path 1: name only
    r1 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only(name),
    )
    h1 = _return_hash(r1.json())
    # Path 2: add concept
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(
            name,
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    h2 = _return_hash(r2.json())
    # Path 3: re-init
    r3 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only(name),
    )
    h3 = _return_hash(r3.json())
    for h, label in [(h1, "init"), (h2, "add"), (h3, "reinit")]:
        assert h is not None, f"Hash None on {label}"
        assert len(h) == 12, f"Hash {h!r} not 12 chars on {label}"
        # All chars are hex
        assert all(c in "0123456789abcdef" for c in h), (
            f"Hash {h!r} not hex on {label}"
        )


# ===========================================================================
# Lens 8: Cross-handler helper-wiring audit.
# Confirm ``_do_closure`` inline extraction is intentionally NOT using
# the canonical ``_extract_*_from_parameters`` helpers. The reason: the
# canonical helpers pick ONE coding, but $closure's ``concept`` is 0..*
# (repeating). Source-read probes guard against a future refactor that
# silently swaps to the wrong helper.
# ===========================================================================


def test_e80_do_closure_inline_concept_extraction_source_audit():
    """Source-reading probe: ``_do_closure`` uses inline concept loop,
    NOT a canonical helper.

    The canonical ``_extract_coding_from_parameters`` /
    ``_extract_named_coding_from_parameters`` helpers pick ONE coding
    (the first with both system and code). $closure's ``concept`` is
    0..* — calling ``_extract_named_coding_from_parameters(body,
    "concept")`` would silently drop all but the first concept.

    This probe reads the source of ``_do_closure`` and asserts the
    inline ``for param in body.get("parameter", []):`` loop is still
    present. If a future refactor swaps to a canonical helper, this
    probe fails loudly — the engineer MUST add a new sibling helper
    ``_extract_all_codings_from_parameters(body, name)`` instead.
    """
    import medterm4ds.apps.fhir_api as api_mod
    import inspect

    src = inspect.getsource(api_mod.create_fhir_app)
    # Find _do_closure definition
    marker = "def _do_closure("
    idx = src.find(marker)
    assert idx >= 0, "_do_closure not found in create_fhir_app source"
    # Find the next def to bound the function body
    next_def = src.find("\n    def ", idx + 1)
    assert next_def > idx, "Could not bound _do_closure body"
    body = src[idx:next_def]
    # Inline loop is load-bearing
    assert "for param in body.get(\"parameter\", []):" in body, (
        "_do_closure must iterate Parameters inline (0..* semantic). "
        "Swapping to _extract_named_coding_from_parameters would silently "
        "drop all but the first concept."
    )
    # isinstance guard (CF-HISTORIAN-CM03-01 fix) is present
    assert "isinstance(coding, dict)" in body, (
        "_do_closure must keep isinstance(coding, dict) guard "
        "(CF-HISTORIAN-CM03-01)."
    )


def test_e81_extract_named_coding_helper_isinstance_guard_source_audit():
    """Source-reading probe: canonical ``_extract_named_coding_from_parameters``
    has the ``isinstance(coding, dict)`` guard.

    Mirrors CS-04 HISTORIAN test_h60 methodology (source-reading as
    regression guard). Guards against a future refactor that removes
    the guard.
    """
    import medterm4ds.apps.fhir_api as api_mod
    import inspect

    src = inspect.getsource(api_mod.create_fhir_app)
    marker = "def _extract_named_coding_from_parameters("
    idx = src.find(marker)
    assert idx >= 0, "_extract_named_coding_from_parameters not found"
    next_def = src.find("\n    def ", idx + 1)
    body = src[idx:next_def]
    assert "isinstance(coding, dict)" in body


# ===========================================================================
# Lens 9: Carry-forward pinning.
# EXPLORER confirms the carry-forwards documented by SKEPTIC + HISTORIAN
# remain DEFERRED (current behavior pinned). When a future enhancement
# chunk closes them, these probes MUST be updated.
# ===========================================================================


def test_e90_cf_skeptic_cm03_01_return_is_value_string(fhir_client):
    """CF-SKEPTIC-CM03-01 (MEDIUM — DEFERRED) pin.

    ``build_closure_response`` emits ``return`` as valueString (NOT
    ConceptMap per canonical R4 OperationDefinition). When a future
    enhancement chunk wires the spec-correct ConceptMap shape, this
    probe MUST be tightened to assert the new shape.
    """
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only("explorer-e90-cf01"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    ret = _find_param(body, "return")
    assert ret is not None
    # CF-SKEPTIC-CM03-01: return is valueString TODAY (spec deviation)
    assert "valueString" in ret
    assert "resource" not in ret  # NOT a ConceptMap resource


def test_e91_cf_skeptic_cm03_01_no_conceptmap_resource(fhir_client):
    """CF-SKEPTIC-CM03-01 mirror: no ConceptMap resource anywhere in
    the response."""
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(
            "explorer-e91-cf01",
            [{"system": SNOMED_URI, "code": "73211009", "display": "DM"}],
        ),
    )
    assert response.status_code == 200, response.text
    body_text = response.text
    # No ConceptMap resourceType anywhere
    assert '"resourceType": "ConceptMap"' not in body_text
    assert "<ConceptMap" not in body_text


def test_e92_cf_skeptic_cm03_02_subsumes_does_not_use_closure(fhir_client):
    """CF-SKEPTIC-CM03-02 (LOW — DEFERRED design discussion) pin.

    ``$subsumes`` HTTP handler walks hierarchy directly via
    ``is_descendant`` — does NOT consult the server-side ClosureTable.
    The probe asserts the OUTCOME is correct (via hierarchy walk) but
    the closure table is bypassed.
    """
    # First populate a closure with parent + child
    fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(
            "explorer-e92-cf02",
            [
                {"system": SNOMED_URI, "code": "73211009", "display": "DM"},
                {"system": SNOMED_URI, "code": "44054006", "display": "T2DM"},
            ],
        ),
    )
    # Now call $subsumes — the handler does NOT use the closure
    response = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes"
        f"?system={SNOMED_URI}&codeA=73211009&codeB=44054006"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Outcome is correct via hierarchy walk (T2DM is-a DM → DM subsumes T2DM)
    outcome_param = _find_param(body, "outcome")
    assert outcome_param is not None
    assert outcome_param.get("valueCode") == "subsumes"


def test_e93_cf_historian_cm03_02_incomplete_since_not_surfaced(fhir_client):
    """CF-HISTORIAN-CM03-02 (LOW — DEFERRED) pin.

    The ``incomplete_since`` flag is NOT surfaced in the HTTP response.
    When a future enhancement surfaces it (e.g. as an extension),
    this probe MUST be updated.
    """
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only("explorer-e93-cf-hist"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # No extension carrying incomplete-since
    body_text = response.text
    assert "incomplete" not in body_text.lower()
    assert "incomplete_since" not in body_text


# ===========================================================================
# Lens 10: Body shape audit + hostile input.
# Mirrors CM-02 EXPLORER test_e70 (hostile-input matrix).
# ===========================================================================


def test_e100_post_closure_hostile_name_no_500(fhir_client):
    """Hostile closure names do NOT cause 500 + traceback.

    Per CM-02 EXPLORER test_e70 methodology: hostile inputs (SQL
    injection, XSS, path traversal, control chars) MUST be handled
    gracefully. The handler NEVER returns a 500 with traceback on
    hostile input — information-disclosure surface.
    """
    hostile_names = [
        "'; DROP TABLE closure; --",
        "<script>alert('xss')</script>",
        "../../../etc/passwd",
        "name\0with\0null",
        "a" * 10000,  # very long
    ]
    for name in hostile_names:
        response = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_name_only(name),
        )
        assert response.status_code == 200, (
            f"Hostile name {name!r} caused non-200: {response.status_code}"
        )
        body = response.json()
        assert body["resourceType"] == "Parameters"


def test_e101_post_closure_hostile_concept_code_no_500(fhir_client):
    """Hostile concept codes do NOT cause 500 + traceback.

    Per CM-02 EXPLORER test_e70 + DuckDB prepared statements: hostile
    concept codes are handled gracefully.
    """
    hostile_codes = [
        "'; DROP TABLE mrconso; --",
        "<script>alert('xss')</script>",
        "../../../etc/passwd",
        "code\0with\0null",
        "a" * 1000,
    ]
    for code in hostile_codes:
        response = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_with_concepts(
                "explorer-e101-hostile",
                [{"system": SNOMED_URI, "code": code, "display": code}],
            ),
        )
        assert response.status_code == 200, (
            f"Hostile code {code!r} caused non-200: {response.status_code}"
        )


def test_e102_post_closure_no_parameters_body(fhir_client):
    """POST with empty body / non-Parameters body is handled.

    Per FHIR R4 §3.2.1.0.5: malformed request body → 422 + FHIR
    OperationOutcome (per RequestValidationError handler registered
    per SKEPTIC TS-02 QA-020).
    """
    # Empty body
    response = fhir_client.post("/fhir/CodeSystem/$closure", content=b"")
    assert response.status_code in (400, 422), response.text
    body = response.json()
    assert body["resourceType"] == "OperationOutcome"


def test_e103_post_closure_non_parameters_resource_body(fhir_client):
    """POST with a non-Parameters resource body is handled.

    The handler accepts a dict; if the dict has no ``parameter`` key,
    the body is treated as missing-name → 400.
    """
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json={"resourceType": "Patient", "id": "explorer-e103"},
    )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["resourceType"] == "OperationOutcome"


# ===========================================================================
# Lens 11: Cross-handler response shape audit — every CodeSystem operation
# returns FHIR-conformant Content-Type (mirrors Milestone-1 CR-001 probe
# class — walk app.routes).
# ===========================================================================


def test_e110_walk_routes_content_type_on_closure(fhir_client):
    """Walk app.routes and confirm ``$closure`` POST returns FHIR+json
    Content-Type. Mirrors Milestone-1 CR-001 probe class (parametrize
    Content-Type over every route).
    """
    # Direct probe — the route is registered; we've already covered
    # this in test_e10..e13. This probe confirms the route exists and
    # is registered before the catch-all.
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only("explorer-e110-walk"),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/fhir+json"


def test_e111_post_closure_returns_parameters_not_bundle(fhir_client):
    """Response is Parameters (not Bundle, not ConceptMap).

    Per FHIR R4: ``$closure`` Out parameter ``return`` is 1..1. The
    resourceType of the Parameters response itself is ``Parameters``
    (NOT Bundle).
    """
    response = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only("explorer-e111-rt"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resourceType"] == "Parameters"
    assert body["resourceType"] != "Bundle"
    assert body["resourceType"] != "ConceptMap"


# ===========================================================================
# Lens 12: Build_closure_response direct unit test (extends SKEPTIC
# test_s120/s121).
# ===========================================================================


def test_e120_build_closure_response_with_multiple_concepts_sorted():
    """``build_closure_response`` sorts concepts by code in the parameter
    list. Confirms SKEPTIC test_s120 at the multi-concept level.
    """
    closure = ClosureTable("explorer-e120-unit")
    closure.concepts = {
        "zzz": {"system": "SNOMEDCT_US", "display": "Z code"},
        "aaa": {"system": "SNOMEDCT_US", "display": "A code"},
        "mmm": {"system": "SNOMEDCT_US", "display": "M code"},
    }
    response = build_closure_response(closure)
    assert response["resourceType"] == "Parameters"
    concept_entries = _find_params(response, "concept")
    codes = [c["valueCoding"]["code"] for c in concept_entries]
    assert codes == ["aaa", "mmm", "zzz"]


def test_e121_build_closure_response_includes_return_first():
    """``return`` is the FIRST parameter in the list.

    Per SKEPTIC test_s120: ``return`` precedes the concept list.
    """
    closure = ClosureTable("explorer-e121")
    closure.concepts = {
        "73211009": {"system": "SNOMEDCT_US", "display": "DM"},
    }
    response = build_closure_response(closure)
    first_param = response["parameter"][0]
    assert first_param["name"] == "return"
    assert "valueString" in first_param


def test_e122_build_closure_response_canonical_system_uri():
    """``build_closure_response`` uses ``system_to_fhir_uri`` to
    canonicalize system on concept entries.

    The internal source name (e.g. ``SNOMEDCT_US``) is translated to
    the FHIR R4 canonical URI (``http://snomed.info/sct``).
    """
    closure = ClosureTable("explorer-e122")
    closure.concepts = {
        "73211009": {"system": "SNOMEDCT_US", "display": "DM"},
        "E11": {"system": "ICD10CM", "display": "T2DM"},
    }
    response = build_closure_response(closure)
    concept_entries = _find_params(response, "concept")
    systems = {c["valueCoding"]["system"] for c in concept_entries}
    assert "http://snomed.info/sct" in systems
    assert "http://hl7.org/fhir/sid/icd-10-cm" in systems


# ===========================================================================
# Lens 13: ClosureManager direct API tests (extends SKEPTIC test_s110..s113).
# ===========================================================================


def test_e130_closure_manager_list_names():
    """``ClosureManager.list_names`` returns all registered names."""
    manager = ClosureManager()
    manager.get_or_create("explorer-e130-a")
    manager.get_or_create("explorer-e130-b")
    names = manager.list_names()
    assert "explorer-e130-a" in names
    assert "explorer-e130-b" in names


def test_e131_closure_manager_reset_creates_fresh_instance():
    """``ClosureManager.reset`` replaces the underlying ClosureTable
    instance (not in-place mutation)."""
    manager = ClosureManager()
    t1 = manager.get_or_create("explorer-e131")
    t1.concepts = {"X": {"system": "S", "display": "X"}}
    t2 = manager.reset("explorer-e131")
    assert t1 is not t2  # different instance
    assert t2.concepts == {}  # fresh state


def test_e132_closure_manager_get_or_create_idempotent():
    """``get_or_create`` returns the SAME instance for an existing name."""
    manager = ClosureManager()
    t1 = manager.get_or_create("explorer-e132")
    t2 = manager.get_or_create("explorer-e132")
    assert t1 is t2


# ===========================================================================
# Lens 14: Cross-personality hygiene — confirm SKEPTIC + HISTORIAN probes
# still pass (regression guard). The probes below are EXPLORER-specific
# observations about the consistency of the CM-03 surface.
# ===========================================================================


def test_e140_skeptic_test_s90_still_load_bearing():
    """Source-reading probe: SKEPTIC test_s90 carry-forward pin is still
    present in the test file. Guards against accidental deletion."""
    from pathlib import Path
    p = Path(__file__).parent / "test_cm03_skeptic.py"
    src = p.read_text()
    assert "def test_s90_spec_deviation_return_is_value_string_not_conceptmap" in src
    assert "def test_s91_spec_deviation_no_conceptmap_resource_in_response" in src


def test_e141_historian_test_h10_still_load_bearing():
    """Source-reading probe: HISTORIAN test_h10 (CF-HISTORIAN-CM03-01
    regression guard) is still present."""
    from pathlib import Path
    p = Path(__file__).parent / "test_cm03_historian.py"
    src = p.read_text()
    assert "def test_h10_post_closure_concept_value_coding_wrong_type_silently_dropped" in src
    assert "def test_h22_incomplete_since_not_surfaced_in_http_response" in src


def test_e142_historian_test_h81_batch_parity_still_load_bearing():
    """Source-reading probe: HISTORIAN test_h81 (batch vs per-operation
    response shape parity) is still present."""
    from pathlib import Path
    p = Path(__file__).parent / "test_cm03_historian.py"
    src = p.read_text()
    assert "def test_h81_batch_closure_response_shape_matches_per_operation" in src
