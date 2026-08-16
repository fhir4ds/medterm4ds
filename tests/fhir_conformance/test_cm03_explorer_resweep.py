"""EXPLORER RESWEEP probes for chunk CM-03 (ConceptMap $closure Operation).

Source: https://build.fhir.org/conceptmap-operation-closure.html
Canonical R4 OperationDefinition:
    https://hl7.org/fhir/R4/conceptmap-operation-closure.html

This resweep extends the baseline ``test_cm03_explorer.py`` with NEW
lateral-combination probes through the EXPLORER lens ("What's not yet
tested?"). Per ``evolution.json.config.notes`` (HISTORIAN tip for
EXPLORER), 5 lateral directions are explored:

  1. **Combined-operations lifecycle** ($closure init -> add ->
     $subsumes -> re-init -> $subsumes state-machine).
  2. **Hostile-name through batch dispatcher** (hostile name exercised
     through the batch Bundle entry path, not just per-op POST).
  3. **XML parity per-op POST vs batch Bundle entry** (byte-exact
     Content-Type + body shape across the two invocation paths).
  4. **Version hash stability across server restart** (forward-looking
     pin: the hash is documented in-memory only — the pin asserts the
     documented non-persistence semantic).
  5. **Batch Bundle shape audit** (entry.resource carries full Parameters
     resource with the correct resourceType and parameter shape).

EXPLORER lens: lateral thinking. The probes below cover COMBINATIONS
and lateral corners that no prior personality has tried.

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus; A44054006 -> A73211009)

Existing baseline coverage in test_cm03_explorer.py: 45 tests across 14
lenses. This resweep does NOT re-derive baseline coverage — it focuses
on NEW lateral combinations per HISTORIAN tip.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from medterm4ds.engines.fhir.closure import (
    ClosureManager,
    ClosureTable,
    build_closure_response,
    get_closure_manager,
)


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------
SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URI_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_URI_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
UNKNOWN_SYSTEM_URI = "http://example.org/unknown-system"

DM_CODE = "73211009"   # Diabetes mellitus
T2DM_CODE = "44054006"  # Type 2 diabetes mellitus


# ---------------------------------------------------------------------------
# Body-shape helpers.
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


def _bundle_with_closure_entry(
    name: str, concepts: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build a Bundle type=batch with one ``$closure`` entry."""
    if concepts:
        resource = _closure_with_concepts(name, concepts)
    else:
        resource = _closure_name_only(name)
    return {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [{
            "request": {
                "method": "POST",
                "url": "/CodeSystem/$closure",
            },
            "resource": resource,
        }],
    }


def _get_func_source(source: str, name: str) -> str:
    """Return source text of a top-level OR nested function by name.

    Walks BOTH ``ast.FunctionDef`` AND ``ast.AsyncFunctionDef`` to catch
    nested async route handlers inside ``create_fhir_app()``.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            try:
                return ast.get_source_segment(source, node) or ""
            except Exception:
                return ""
    return ""


def _get_nested_func_source(source: str, parent_name: str, child_name: str) -> str:
    """Return source text of a function defined INSIDE another function.

    Used to read ``_do_closure`` (defined inside ``create_fhir_app``) and
    ``_dispatch_batch_operation`` (also inside ``create_fhir_app``).
    Mirrors CS-03 HISTORIAN strategy 11.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == parent_name):
            # Search for the nested child def inside the parent's body
            for child in ast.walk(node):
                if (isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and child.name == child_name):
                    try:
                        return ast.get_source_segment(source, child) or ""
                    except Exception:
                        return ""
    return ""


# ===========================================================================
# Lens 1: Combined-operations lifecycle.
# (HISTORIAN tip 1: $closure init -> add -> $subsumes -> re-init ->
# $subsumes state-machine)
#
# The closure table's state-machine has the following transitions:
#   init (name only) -> empty ClosureTable (no concepts)
#   add  (name + concepts) -> ClosureTable with concepts (version bumped)
#   re-init (name only again) -> empty ClosureTable (state cleared)
#
# EXPLORER extends SKEPTIC test_s40 (init -> add -> re-init -> add) by
# inserting $subsumes between transitions to verify the closure table's
# effect on $subsumes IS observable AND is consistent across transitions.
#
# NOTE: per CF-SKEPTIC-CM03-02 (DEFERRED), $subsumes does NOT consult the
# server-side ClosureTable. The probes verify the OUTCOME is correct (via
# hierarchy walk) and that the closure's state machine doesn't corrupt
# the hierarchy-walked result.
# ===========================================================================


def test_e10_combined_lifecycle_init_add_subsumes_reinit_subsumes(fhir_client):
    """L1a: full state-machine roundtrip.

    Sequence:
      1. init (name only) — version hash V0
      2. add DM + T2DM — version hash V1 (state populated)
      3. $subsumes(DM, T2DM) — should return "subsumes" via hierarchy
      4. re-init (name only) — version hash back to V0-equivalent
      5. $subsumes(DM, T2DM) — STILL "subsumes" (hierarchy unaffected by
         closure reset; per CF-SKEPTIC-CM03-02, $subsumes walks hierarchy
         directly).

    Asserts: each transition returns 200; the closure version hash
    reflects the state transitions; $subsumes outcome is consistent
    across transitions.
    """
    name = "explorer-e10-lifecycle"
    # 1. init
    r1 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only(name),
    )
    assert r1.status_code == 200, r1.text
    hash_init = _return_hash(r1.json())
    assert hash_init is not None

    # 2. add DM + T2DM
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(name, [
            {"system": SNOMED_URI, "code": DM_CODE, "display": "DM"},
            {"system": SNOMED_URI, "code": T2DM_CODE, "display": "T2DM"},
        ]),
    )
    assert r2.status_code == 200, r2.text
    hash_populated = _return_hash(r2.json())
    concepts_populated = _find_params(r2.json(), "concept")
    assert len(concepts_populated) == 2

    # 3. $subsumes(DM, T2DM) -> "subsumes"
    r3 = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes"
        f"?system={SNOMED_URI}&codeA={DM_CODE}&codeB={T2DM_CODE}"
    )
    assert r3.status_code == 200, r3.text
    body3 = r3.json()
    outcome3 = _find_param(body3, "outcome")
    assert outcome3 is not None
    assert outcome3.get("valueCode") == "subsumes"

    # 4. re-init (name only)
    r4 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only(name),
    )
    assert r4.status_code == 200, r4.text
    hash_after_reinit = _return_hash(r4.json())
    concepts_reinit = _find_params(r4.json(), "concept")
    assert len(concepts_reinit) == 0

    # 5. $subsumes(DM, T2DM) STILL "subsumes" (per CF-SKEPTIC-CM03-02:
    #    $subsumes walks hierarchy directly; closure state is bypassed)
    r5 = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes"
        f"?system={SNOMED_URI}&codeA={DM_CODE}&codeB={T2DM_CODE}"
    )
    assert r5.status_code == 200, r5.text
    body5 = r5.json()
    outcome5 = _find_param(body5, "outcome")
    assert outcome5 is not None
    assert outcome5.get("valueCode") == "subsumes"

    # Hash transitions are consistent: init != populated; reinit != populated
    assert hash_init != hash_populated
    assert hash_after_reinit != hash_populated


def test_e11_combined_lifecycle_subsumes_then_init_does_not_break_subsumes(fhir_client):
    """L1b: $subsumes works BEFORE the closure is initialized.

    The closure table is keyed by name; an unknown name produces a
    "fresh" closure (per ClosureManager.get_or_create). The hierarchy
    walk should still return the correct outcome.

    Sequence:
      1. $subsumes(DM, T2DM) — closure never initialized
      2. init closure with name "X"
      3. $subsumes(DM, T2DM) — closure initialized but EMPTY (no concepts)
      4. Both outcomes MUST be "subsumes" via hierarchy walk.

    Asserts: no 5xx; outcomes consistent; closure init doesn't corrupt
    hierarchy-walked $subsumes.
    """
    name = "explorer-e11-pre-init"
    # 1. $subsumes BEFORE init
    r1 = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes"
        f"?system={SNOMED_URI}&codeA={DM_CODE}&codeB={T2DM_CODE}"
    )
    assert r1.status_code == 200, r1.text
    outcome1 = _find_param(r1.json(), "outcome")
    assert outcome1 is not None
    assert outcome1.get("valueCode") == "subsumes"

    # 2. init closure
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only(name),
    )
    assert r2.status_code == 200, r2.text

    # 3. $subsumes AFTER init (closure is empty)
    r3 = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes"
        f"?system={SNOMED_URI}&codeA={DM_CODE}&codeB={T2DM_CODE}"
    )
    assert r3.status_code == 200, r3.text
    outcome3 = _find_param(r3.json(), "outcome")
    assert outcome3 is not None
    assert outcome3.get("valueCode") == "subsumes"

    # 4. Consistent outcomes
    assert outcome1.get("valueCode") == outcome3.get("valueCode")


def test_e12_combined_lifecycle_check_api_works_post_reset(fhir_client):
    """L1c: ClosureTable.check() Python API works correctly after reset.

    Sequence:
      1. Init manager + table; add DM + T2DM
      2. Check DM subsumes T2DM via Python API -> "subsumes"
      3. Reset the closure (fresh ClosureTable)
      4. Check DM subsumes T2DM via Python API -> "not-subsumed"
         (codes not in closure after reset)

    Asserts: ClosureTable.check() returns correct outcomes pre/post
    reset; the manager's reset fully clears the table.
    """
    manager = ClosureManager()
    name = "explorer-e12-check-api"
    # 1. create + add
    table = manager.get_or_create(name)
    # Don't add via add_concepts (needs engine); just populate concepts dict.
    # QC-266: concepts/subsumes are keyed by (source, code) pairs.
    dm_key = ("SNOMEDCT_US", DM_CODE)
    t2dm_key = ("SNOMEDCT_US", T2DM_CODE)
    table.concepts = {
        dm_key: {"system": "SNOMEDCT_US", "display": "DM"},
        t2dm_key: {"system": "SNOMEDCT_US", "display": "T2DM"},
    }
    # Manually populate subsumption (DM subsumes T2DM)
    table._subsumes = {
        (dm_key, dm_key): True,
        (t2dm_key, t2dm_key): True,
        (dm_key, t2dm_key): True,
        (t2dm_key, dm_key): False,
    }
    # 2. Check DM subsumes T2DM
    assert table.check(DM_CODE, T2DM_CODE, "SNOMEDCT_US") == "subsumes"
    assert table.check(T2DM_CODE, DM_CODE, "SNOMEDCT_US") == "subsumed-by"
    assert table.check(DM_CODE, DM_CODE, "SNOMEDCT_US") == "equivalent"

    # 3. Reset the closure
    table_after = manager.reset(name)
    assert table_after is not table  # fresh instance

    # 4. After reset, codes not in closure -> not-subsumed
    assert table_after.check(DM_CODE, T2DM_CODE, "SNOMEDCT_US") == "not-subsumed"
    # The OLD table instance still has its data (not mutated)
    assert table.check(DM_CODE, T2DM_CODE, "SNOMEDCT_US") == "subsumes"


def test_e13_combined_lifecycle_three_cycles_no_state_leak(fhir_client):
    """L1d: Three init -> add -> re-init cycles produce no state leak.

    A subtle state-leak bug: if `ClosureManager.reset` mutated the
    existing ClosureTable in-place instead of constructing a fresh
    instance, the second cycle's hash would differ from the first
    cycle's hash. EXPLORER verifies the hash is identical across 3
    cycles.
    """
    name = "explorer-e13-three-cycles"
    hashes_per_cycle_init = []
    hashes_per_cycle_after_add = []

    for cycle in range(3):
        # init
        r_init = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_name_only(name),
        )
        assert r_init.status_code == 200, r_init.text
        h_init = _return_hash(r_init.json())
        hashes_per_cycle_init.append(h_init)

        # add DM + T2DM
        r_add = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_with_concepts(name, [
                {"system": SNOMED_URI, "code": DM_CODE, "display": "DM"},
                {"system": SNOMED_URI, "code": T2DM_CODE, "display": "T2DM"},
            ]),
        )
        assert r_add.status_code == 200, r_add.text
        h_add = _return_hash(r_add.json())
        hashes_per_cycle_after_add.append(h_add)

    # All 3 init hashes are identical (no state leak across cycles)
    assert len(set(hashes_per_cycle_init)) == 1, (
        f"Init hashes differ across cycles: {hashes_per_cycle_init}"
    )
    # All 3 after-add hashes are identical
    assert len(set(hashes_per_cycle_after_add)) == 1, (
        f"After-add hashes differ across cycles: {hashes_per_cycle_after_add}"
    )


# ===========================================================================
# Lens 2: Hostile-name through batch dispatcher.
# (HISTORIAN tip 2: hostile name exercised through the batch Bundle entry
# path, not just per-op POST)
#
# The batch dispatcher path (``_dispatch_batch_operation`` -> ``_do_closure``)
# is structurally distinct from the per-op POST path
# (``closure_post`` -> ``_do_closure``). EXPLORER verifies that hostile
# names that are accepted by the per-op path are ALSO accepted by the
# batch path with the same isolation properties.
# ===========================================================================


HOSTILE_NAMES = [
    "'; DROP TABLE closure; --",
    "<script>alert('xss')</script>",
    "../../../etc/passwd",
    "name\0with\0null",
    "a" * 10000,
    "    ",
    "name\r\nX-Inject: header",
    "關閉表",  # Unicode CJK
]


@pytest.mark.parametrize("hostile_name", HOSTILE_NAMES,
                          ids=lambda v: f"hostile-{len(v)}-{hash(v) % 1000}")
def test_e20_batch_dispatcher_accepts_hostile_name(fhir_client, hostile_name):
    """L2a: Hostile name through batch dispatcher does not 500.

    Mirrors SKEPTIC test_s10 (per-op POST hostile name) on the batch
    path. Per FHIR R4 §3.7: batch entries are independent.
    """
    r = fhir_client.post(
        "/fhir",
        json=_bundle_with_closure_entry(hostile_name),
    )
    assert r.status_code == 200, r.text
    bundle = r.json()
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "batch-response"
    assert len(bundle["entry"]) == 1
    entry = bundle["entry"][0]
    assert entry["response"]["status"] == "200"
    assert entry["resource"]["resourceType"] == "Parameters"
    # The return parameter (version hash) is present
    assert _find_param(entry["resource"], "return") is not None


def test_e21_batch_dispatcher_hostile_name_per_entry_isolation(fhir_client):
    """L2b: Hostile name in one batch entry does NOT corrupt sibling entry.

    Per FHIR R4 §3.7: success or failure of one entry MUST NOT alter
    another. EXPLORER probes a batch with a hostile-name entry AND a
    well-formed entry; both should produce independent results.
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
                    "resource": _closure_name_only("'; DROP TABLE closure; --"),
                },
                {
                    "request": {
                        "method": "POST",
                        "url": "/CodeSystem/$closure",
                    },
                    "resource": _closure_name_only("explorer-e21-sibling"),
                },
            ],
        },
    )
    assert r.status_code == 200, r.text
    bundle = r.json()
    assert len(bundle["entry"]) == 2
    # Both entries are 200 Parameters (hostile name accepted)
    for i, entry in enumerate(bundle["entry"]):
        assert entry["response"]["status"] == "200", (
            f"Entry {i} status: {entry['response']['status']}"
        )
        assert entry["resource"]["resourceType"] == "Parameters"
    # The two closures have DIFFERENT version hashes (name is NOT part of
    # the hash payload, but the closures are DIFFERENT instances in the
    # manager's _tables dict — both should produce the same hash since
    # both have zero concepts)
    hashes = [
        _return_hash(e["resource"]) for e in bundle["entry"]
    ]
    # Both have a hash
    assert all(h is not None for h in hashes)
    # Both hashes are 12-char MD5 hex
    for h in hashes:
        assert len(h) == 12
        assert all(c in "0123456789abcdef" for c in h)


def test_e22_batch_dispatcher_hostile_name_full_lifecycle(fhir_client):
    """L2c: Hostile name exercised through full lifecycle via batch.

    Sequence (all entries in ONE batch):
      entry[0]: init with hostile name
      entry[1]: add concepts to same hostile name
      entry[2]: re-init same hostile name

    Per FHIR R4 §3.7: order preservation is structural. EXPLORER
    verifies each entry produces the correct transition.
    """
    hostile = "<script>alert('xss')</script>"
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                # init
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": _closure_name_only(hostile),
                },
                # add concepts
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": _closure_with_concepts(hostile, [
                        {"system": SNOMED_URI, "code": DM_CODE, "display": "DM"},
                    ]),
                },
                # re-init
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": _closure_name_only(hostile),
                },
            ],
        },
    )
    assert r.status_code == 200, r.text
    bundle = r.json()
    assert len(bundle["entry"]) == 3
    # All 3 entries 200 Parameters
    for entry in bundle["entry"]:
        assert entry["response"]["status"] == "200"
        assert entry["resource"]["resourceType"] == "Parameters"
    # entry[0]: init (0 concepts)
    e0_concepts = _find_params(bundle["entry"][0]["resource"], "concept")
    assert len(e0_concepts) == 0
    # entry[1]: add (1 concept)
    e1_concepts = _find_params(bundle["entry"][1]["resource"], "concept")
    assert len(e1_concepts) == 1
    # entry[2]: re-init (0 concepts again)
    e2_concepts = _find_params(bundle["entry"][2]["resource"], "concept")
    assert len(e2_concepts) == 0


# ===========================================================================
# Lens 3: XML parity per-op POST vs batch Bundle entry.
# (HISTORIAN tip 3: byte-exact Content-Type + body shape across the two
# invocation paths)
#
# EXPLORER verifies that requesting XML on the per-op POST AND on the
# batch dispatcher entry produces conformant XML bodies with matching
# Content-Type. Per-op POST uses ``_fhir_response``; batch uses
# ``_process_batch_entry`` which wraps the resource in a Bundle entry.
# The XML serializer must produce equivalent <Parameters>...</Parameters>
# content (modulo the entry envelope).
# ===========================================================================


def test_e30_xml_per_op_post_content_type(fhir_client):
    """L3a: per-op POST _format=xml returns application/fhir+xml."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure?_format=xml",
        json=_closure_name_only("explorer-e30-perop-xml"),
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/fhir+xml"
    body = r.text
    assert "<Parameters" in body
    # The XML serializer renders <parameter><name value="return"/><valueString .../></parameter>
    assert 'value="return"' in body
    assert "valueString" in body


def test_e31_xml_batch_dispatcher_content_type(fhir_client):
    """L3b: batch Bundle entry with _format=xml returns application/fhir+xml.

    Per FHIR R4 §3.7: a batch Bundle response has Content-Type
    application/fhir+xml when the client requested XML. The entry's
    resource (Parameters) is XML-serialized inside the Bundle entry.
    """
    r = fhir_client.post(
        "/fhir?_format=xml",
        json=_bundle_with_closure_entry("explorer-e31-batch-xml"),
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/fhir+xml"
    body = r.text
    assert "<Bundle" in body
    # The entry's resource (Parameters) is XML-serialized via resourceType
    assert 'value="Parameters"' in body
    # return parameter is serialized
    assert 'value="return"' in body
    assert "valueString" in body


def test_e32_xml_per_op_vs_batch_equivalent_parameter_shape(fhir_client):
    """L3c: XML body shape (parameter count, names) is equivalent across
    per-op POST and batch dispatcher.

    Not byte-exact (batch Bundle wraps the Parameters in an entry
    envelope), but the Parameters content (parameter names + value
    types) MUST match.
    """
    name_perop = "explorer-e32-perop-xml"
    name_batch = "explorer-e32-batch-xml"
    concepts = [
        {"system": SNOMED_URI, "code": DM_CODE, "display": "DM"},
        {"system": SNOMED_URI, "code": T2DM_CODE, "display": "T2DM"},
    ]
    # per-op POST
    r_perop = fhir_client.post(
        "/fhir/CodeSystem/$closure?_format=xml",
        json=_closure_with_concepts(name_perop, concepts),
    )
    assert r_perop.status_code == 200, r_perop.text
    # batch POST
    r_batch = fhir_client.post(
        "/fhir?_format=xml",
        json=_bundle_with_closure_entry(name_batch, concepts),
    )
    assert r_batch.status_code == 200, r_batch.text
    # Both bodies have <return valueString="..."/>
    assert "valueString" in r_perop.text
    assert "valueString" in r_batch.text
    # Both have <valueCoding> entries for the concepts
    assert "<valueCoding" in r_perop.text
    assert "<valueCoding" in r_batch.text
    # Concept codes appear in both
    assert DM_CODE in r_perop.text
    assert DM_CODE in r_batch.text
    assert T2DM_CODE in r_perop.text
    assert T2DM_CODE in r_batch.text


def test_e33_xml_per_op_error_path_content_type(fhir_client):
    """L3d: per-op POST error path with _format=xml returns
    application/fhir+xml + OperationOutcome."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure?_format=xml",
        json={"resourceType": "Parameters", "parameter": []},
    )
    assert r.status_code == 400, r.text
    assert r.headers["content-type"] == "application/fhir+xml"
    assert "<OperationOutcome" in r.text


def test_e34_xml_accept_header_per_op_post(fhir_client):
    """L3e: Accept: application/fhir+xml on per-op POST produces XML.

    Per FHIR R4 §3.1.0.1.10: Accept header negotiation MUST produce the
    requested format.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only("explorer-e34-accept"),
        headers={"Accept": "application/fhir+xml"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/fhir+xml"
    assert "<Parameters" in r.text


# ===========================================================================
# Lens 4: Version hash stability across server restart.
# (HISTORIAN tip 4: forward-looking pin)
#
# The closure table is IN-MEMORY ONLY per the module docstring
# (``engines/fhir/closure.py`` line 9). The hash is deterministic across
# ClosureManager instances BUT the tables are NOT persisted across
# server restarts.
#
# EXPLORER verifies:
#   - The hash is deterministic across two NEW ClosureManager instances
#     with the same state (verifies no implicit global state contaminates
#     the hash).
#   - The hash payload composition is documented (forward-looking pin).
#   - The singleton manager pattern (``get_closure_manager``) preserves
#     state across multiple invocations within ONE server process.
# ===========================================================================


def test_e40_hash_deterministic_across_two_manager_instances():
    """L4a: Two separate ClosureManager instances produce the same hash
    for the same closure state."""
    m1 = ClosureManager()
    m2 = ClosureManager()
    t1 = m1.get_or_create("explorer-e40-determinism")
    t2 = m2.get_or_create("explorer-e40-determinism")
    # EC-11 QC-266: (source, code) keys.
    t1.concepts = {
        ("SNOMEDCT_US", DM_CODE): {"system": "SNOMEDCT_US", "display": "DM"},
        ("SNOMEDCT_US", T2DM_CODE): {"system": "SNOMEDCT_US", "display": "T2DM"},
    }
    t2.concepts = {
        ("SNOMEDCT_US", DM_CODE): {"system": "SNOMEDCT_US", "display": "DM"},
        ("SNOMEDCT_US", T2DM_CODE): {"system": "SNOMEDCT_US", "display": "T2DM"},
    }
    assert t1.version_hash() == t2.version_hash()


def test_e41_hash_payload_documented_in_source():
    """L4b: forward-looking pin — the version_hash payload composition
    is documented via source-read.

    Per the module docstring (engines/fhir/closure.py line 9): "The
    closure tables are in-memory (lost on server restart)." This pin
    documents the hash payload composition so a future enhancement that
    persists the hash across restarts MUST update this probe.
    """
    closure_path = (
        Path(__file__).resolve().parents[2]
        / "src" / "medterm4ds" / "engines" / "fhir" / "closure.py"
    )
    src = closure_path.read_text()
    # EC-11 QC-270/QC-283: the payload covers the FULL state (concepts
    # AND relations) and excludes the internal call counter.
    assert "relation_items" in src
    assert "concept_items" in src
    # MD5 algorithm is pinned
    assert "hashlib.md5" in src
    # [:12] truncation is pinned
    assert "[:12]" in src
    # The in-memory docstring is present (forward-looking pin for
    # persistence enhancement)
    assert "in-memory" in src.lower() or "lost on server restart" in src.lower()


def test_e42_singleton_manager_state_preserved_within_process():
    """L4c: ``get_closure_manager`` returns the SAME instance across
    multiple invocations within ONE process — state is preserved.

    (Across processes, state IS lost per the module docstring; this
    probe does NOT test that — it documents the within-process
    invariant.)
    """
    m1 = get_closure_manager()
    m2 = get_closure_manager()
    assert m1 is m2  # same instance
    # State is preserved
    name = "explorer-e42-state-preserved"
    t1 = m1.get_or_create(name)
    t1.concepts = {"X": {"system": "S", "display": "X"}}
    t2 = m2.get_or_create(name)
    assert t2 is t1  # same table
    assert "X" in t2.concepts


def test_e43_hash_format_consistent_across_three_paths(fhir_client):
    """L4d: hash format (12-char MD5 hex) is consistent across the three
    closure lifecycle paths: init, add, re-init.

    Confirms SKEPTIC test_s22 + extends: format consistency verified
    across multiple state transitions.
    """
    name = "explorer-e43-format"
    hashes = []
    # init
    r1 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only(name),
    )
    hashes.append(_return_hash(r1.json()))
    # add
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(name, [
            {"system": SNOMED_URI, "code": DM_CODE, "display": "DM"},
        ]),
    )
    hashes.append(_return_hash(r2.json()))
    # add more
    r3 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(name, [
            {"system": SNOMED_URI, "code": T2DM_CODE, "display": "T2DM"},
        ]),
    )
    hashes.append(_return_hash(r3.json()))
    # re-init
    r4 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only(name),
    )
    hashes.append(_return_hash(r4.json()))
    # All hashes are 12-char MD5 hex
    for h, label in zip(hashes, ["init", "add1", "add2", "reinit"]):
        assert h is not None, f"hash None on {label}"
        assert len(h) == 12, f"hash {h!r} not 12 chars on {label}"
        assert all(c in "0123456789abcdef" for c in h), (
            f"hash {h!r} not hex on {label}"
        )


# ===========================================================================
# Lens 5: Batch Bundle shape audit.
# (HISTORIAN tip 5: entry.resource carries full Parameters)
#
# Per FHIR R4 §3.7: a batch-response Bundle entry has shape:
#   {
#     "response": {"status": "200"},
#     "resource": <FHIR resource returned by the operation>
#   }
#
# EXPLORER verifies the entry.resource field carries the FULL Parameters
# resource with the correct resourceType + parameter shape.
# ===========================================================================


def test_e50_batch_response_bundle_shape(fhir_client):
    """L5a: batch-response Bundle has correct shape per FHIR R4 §3.7."""
    r = fhir_client.post(
        "/fhir",
        json=_bundle_with_closure_entry("explorer-e50-shape"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Top-level Bundle
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "batch-response"
    assert "entry" in body
    assert isinstance(body["entry"], list)
    assert len(body["entry"]) == 1
    # Entry shape
    entry = body["entry"][0]
    assert "response" in entry
    assert "status" in entry["response"]
    assert entry["response"]["status"] == "200"
    # Resource is present and is Parameters
    assert "resource" in entry
    assert entry["resource"]["resourceType"] == "Parameters"


def test_e51_batch_entry_resource_carries_full_parameters(fhir_client):
    """L5b: entry.resource carries the FULL Parameters resource —
    return parameter + concept parameters are all present."""
    name = "explorer-e51-full-parameters"
    concepts = [
        {"system": SNOMED_URI, "code": DM_CODE, "display": "DM"},
        {"system": SNOMED_URI, "code": T2DM_CODE, "display": "T2DM"},
    ]
    r = fhir_client.post(
        "/fhir",
        json=_bundle_with_closure_entry(name, concepts),
    )
    assert r.status_code == 200, r.text
    entry_resource = r.json()["entry"][0]["resource"]
    # return parameter (first per builder order)
    return_param = _find_param(entry_resource, "return")
    assert return_param is not None
    assert "valueString" in return_param
    assert len(return_param["valueString"]) == 12
    # 2 concept parameters (sorted by code)
    concept_params = _find_params(entry_resource, "concept")
    assert len(concept_params) == 2
    codes = [c["valueCoding"]["code"] for c in concept_params]
    assert codes == sorted([DM_CODE, T2DM_CODE])


def test_e52_batch_entry_resource_field_present_on_error(fhir_client):
    """L5c: batch entry's resource field is an OperationOutcome on error.

    Per FHIR R4 §3.7: per-entry error produces a per-entry
    OperationOutcome (not 4xx for the whole bundle).
    """
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [{
                "request": {
                    "method": "POST",
                    "url": "/CodeSystem/$closure",
                },
                # Missing name
                "resource": {"resourceType": "Parameters", "parameter": []},
            }],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "batch-response"
    entry = body["entry"][0]
    assert entry["response"]["status"] == "400"
    assert entry["resource"]["resourceType"] == "OperationOutcome"


def test_e53_batch_three_entry_order_preservation_with_full_parameters(fhir_client):
    """L5d: batch-response entry order matches request entry order;
    each entry.resource carries the full Parameters resource."""
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                # entry[0]: init "name-a"
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": _closure_name_only("explorer-e53-a"),
                },
                # entry[1]: error (missing name)
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": {"resourceType": "Parameters", "parameter": []},
                },
                # entry[2]: init "name-c"
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": _closure_name_only("explorer-e53-c"),
                },
            ],
        },
    )
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 3
    # Order preserved
    # entry[0]: 200 Parameters
    assert entries[0]["response"]["status"] == "200"
    assert entries[0]["resource"]["resourceType"] == "Parameters"
    # entry[1]: 400 OperationOutcome
    assert entries[1]["response"]["status"] == "400"
    assert entries[1]["resource"]["resourceType"] == "OperationOutcome"
    # entry[2]: 200 Parameters
    assert entries[2]["response"]["status"] == "200"
    assert entries[2]["resource"]["resourceType"] == "Parameters"
    # entry[0] and entry[2] have DIFFERENT version hashes IF names were part
    # of the payload. Since names are NOT part of the payload, both produce
    # the SAME hash (both have zero concepts). The probe documents this.
    hash0 = _return_hash(entries[0]["resource"])
    hash2 = _return_hash(entries[2]["resource"])
    assert hash0 is not None
    assert hash2 is not None
    # Both hashes equal because hash payload excludes name + both are empty
    assert hash0 == hash2


# ===========================================================================
# Lens 6: Source-read structural contracts via AST.
# Confirm load-bearing patterns survive refactors via AST source reads
# of nested functions inside create_fhir_app.
# ===========================================================================


def test_e60_dispatch_batch_operation_closure_branch_calls_do_closure():
    """L6a: source-read — _dispatch_batch_operation /CodeSystem/$closure
    branch calls _do_closure (single-source-of-truth)."""
    import medterm4ds.apps.fhir_api as api_mod
    src = inspect.getsource(api_mod.create_fhir_app)
    body = _get_nested_func_source(src, "_dispatch_batch_operation", "_dispatch_batch_operation")
    # If _dispatch_batch_operation is a top-level function in create_fhir_app,
    # _get_nested_func_source returns "". Fall back to direct search.
    if not body:
        # Search for the function def directly
        marker = "async def _dispatch_batch_operation("
        idx = src.find(marker)
        if idx < 0:
            marker = "def _dispatch_batch_operation("
            idx = src.find(marker)
        assert idx >= 0, "_dispatch_batch_operation not found"
        next_def = src.find("\n    async def ", idx + 1)
        if next_def < 0:
            next_def = src.find("\n    def ", idx + 1)
        body = src[idx:next_def] if next_def > idx else src[idx:]
    assert "/CodeSystem/$closure" in body
    assert "_do_closure" in body


def test_e61_dispatch_batch_operation_closure_branch_validates_name():
    """L6b: source-read — _dispatch_batch_operation /CodeSystem/$closure
    branch validates name (per-entry 400 on missing name)."""
    import medterm4ds.apps.fhir_api as api_mod
    src = inspect.getsource(api_mod.create_fhir_app)
    marker = "async def _dispatch_batch_operation("
    idx = src.find(marker)
    if idx < 0:
        marker = "def _dispatch_batch_operation("
        idx = src.find(marker)
    assert idx >= 0, "_dispatch_batch_operation not found"
    next_def = src.find("\n    async def ", idx + 1)
    if next_def < 0:
        next_def = src.find("\n    def ", idx + 1)
    body = src[idx:next_def] if next_def > idx else src[idx:]
    # The closure branch has a name validation block
    assert "/CodeSystem/$closure" in body
    # name validation produces a 400 batch error entry
    assert "name parameter is required for $closure" in body
    assert "_batch_error_entry" in body


def test_e62_do_closure_inline_concept_loop_present():
    """L6c: source-read — _do_closure retains inline concept extraction
    loop (NOT a canonical helper — the repeating 0..* semantic requires
    inline iteration)."""
    import medterm4ds.apps.fhir_api as api_mod
    src = inspect.getsource(api_mod.create_fhir_app)
    marker = "def _do_closure("
    idx = src.find(marker)
    assert idx >= 0, "_do_closure not found"
    next_def = src.find("\n    def ", idx + 1)
    assert next_def > idx, "Could not bound _do_closure"
    body = src[idx:next_def]
    # Inline loop is load-bearing (EC-11 QC-001: iterates the defensive
    # _parameter_entries helper rather than the raw body.get)
    assert 'for param in _parameter_entries(body):' in body
    # isinstance guard (CF-HISTORIAN-CM03-01 fix) is present
    assert "isinstance(param, dict)" in body
    assert "isinstance(coding, dict)" in body


def test_e63_do_closure_dispatches_to_reset_on_init_path():
    """L6d: source-read — _do_closure calls manager.reset(name) on the
    init path (no concepts)."""
    import medterm4ds.apps.fhir_api as api_mod
    src = inspect.getsource(api_mod.create_fhir_app)
    marker = "def _do_closure("
    idx = src.find(marker)
    assert idx >= 0
    next_def = src.find("\n    def ", idx + 1)
    body = src[idx:next_def]
    assert "manager.reset(name)" in body


def test_e64_do_closure_dispatches_to_get_or_create_on_add_path():
    """L6e: source-read — _do_closure calls manager.get_or_create(name)
    AND closure.add_concepts on the add path (concepts present)."""
    import medterm4ds.apps.fhir_api as api_mod
    src = inspect.getsource(api_mod.create_fhir_app)
    marker = "def _do_closure("
    idx = src.find(marker)
    assert idx >= 0
    next_def = src.find("\n    def ", idx + 1)
    body = src[idx:next_def]
    assert "manager.get_or_create(name)" in body
    # EC-11 QC-282: the add path canonicalizes displays via the engine
    # first, then adds the resolved list.
    assert "closure.add_concepts(resolved, engine)" in body


def test_e65_do_closure_calls_build_closure_response():
    """L6f: source-read — _do_closure calls build_closure_response
    (single-source-of-truth for the response builder)."""
    import medterm4ds.apps.fhir_api as api_mod
    src = inspect.getsource(api_mod.create_fhir_app)
    marker = "def _do_closure("
    idx = src.find(marker)
    assert idx >= 0
    next_def = src.find("\n    def ", idx + 1)
    body = src[idx:next_def]
    assert "build_closure_response" in body


def test_e66_closure_post_handler_validates_name_before_run_db():
    """L6g: source-read — closure_post handler validates name BEFORE
    delegating to _run_db (early 400-return)."""
    import medterm4ds.apps.fhir_api as api_mod
    src = inspect.getsource(api_mod.create_fhir_app)
    marker = "async def closure_post("
    idx = src.find(marker)
    assert idx >= 0
    next_def = src.find("\n    async def ", idx + 1)
    if next_def < 0:
        next_def = src.find("\n    def ", idx + 1)
    body = src[idx:next_def] if next_def > idx else src[idx:]
    assert 'name parameter is required for $closure' in body
    assert '_fhir_error_response' in body or '_fhir_error' in body


# ===========================================================================
# Lens 7: Batch hostile-concept isolation.
# EXPLORER extends SKEPTIC test_s28 (per-op mixed valid/invalid concepts)
# to the batch dispatcher path.
# ===========================================================================


def test_e70_batch_malformed_value_coding_isolation(fhir_client):
    """L7a: malformed valueCoding in batch entry is silently dropped
    (per CF-HISTORIAN-CM03-01 fix, the isinstance guard).

    The batch dispatcher path goes through the same _do_closure handler;
    the guard must fire identically on batch path.
    """
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [{
                "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "name", "valueString": "explorer-e70-batch-malformed"},
                        # Valid
                        {"name": "concept", "valueCoding": {
                            "system": SNOMED_URI, "code": DM_CODE, "display": "DM"
                        }},
                        # Malformed (string)
                        {"name": "concept", "valueCoding": "not-a-dict"},
                        # Malformed (int)
                        {"name": "concept", "valueCoding": 42},
                        # Malformed (list)
                        {"name": "concept", "valueCoding": ["x", "y"]},
                    ],
                },
            }],
        },
    )
    assert r.status_code == 200, r.text
    entry = r.json()["entry"][0]
    assert entry["response"]["status"] == "200"
    # Only the one valid concept made it through
    concepts = _find_params(entry["resource"], "concept")
    assert len(concepts) == 1
    assert concepts[0]["valueCoding"]["code"] == DM_CODE


def test_e71_batch_mixed_valid_invalid_entries_isolation(fhir_client):
    """L7b: batch with mix of valid + invalid entries; per-entry isolation."""
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                # Entry 0: valid init
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": _closure_name_only("explorer-e71-valid"),
                },
                # Entry 1: missing name -> 400
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": {"resourceType": "Parameters", "parameter": []},
                },
                # Entry 2: malformed valueCoding (silently dropped)
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": {
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "name", "valueString": "explorer-e71-malformed"},
                            {"name": "concept", "valueCoding": "not-a-dict"},
                        ],
                    },
                },
            ],
        },
    )
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 3
    # Entry 0: 200
    assert entries[0]["response"]["status"] == "200"
    assert entries[0]["resource"]["resourceType"] == "Parameters"
    # Entry 1: 400
    assert entries[1]["response"]["status"] == "400"
    assert entries[1]["resource"]["resourceType"] == "OperationOutcome"
    # Entry 2: QC-264 (HIGH) — an entry whose ONLY concept entry is
    # malformed is a per-entry 400 OperationOutcome (never a silent
    # reset).
    assert entries[2]["response"]["status"] == "400"
    assert entries[2]["resource"]["resourceType"] == "OperationOutcome"


def test_e72_batch_interleaved_op_isolation(fhir_client):
    """L7c: batch with $closure and $lookup interleaved; per-entry
    isolation across operations."""
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                # $closure init
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": _closure_name_only("explorer-e72-interleaved"),
                },
                # $lookup (GET -> params)
                {
                    "request": {
                        "method": "GET",
                        "url": f"/CodeSystem/$lookup?system={SNOMED_URI}&code={DM_CODE}",
                    },
                },
                # $closure add
                {
                    "request": {"method": "POST", "url": "/CodeSystem/$closure"},
                    "resource": _closure_with_concepts(
                        "explorer-e72-interleaved",
                        [{"system": SNOMED_URI, "code": DM_CODE, "display": "DM"}],
                    ),
                },
            ],
        },
    )
    assert r.status_code == 200, r.text
    entries = r.json()["entry"]
    assert len(entries) == 3
    # All three succeed
    for i, entry in enumerate(entries):
        assert entry["response"]["status"] == "200", (
            f"Entry {i} status: {entry['response']['status']}"
        )
    # Entry 0: Parameters (closure init)
    assert entries[0]["resource"]["resourceType"] == "Parameters"
    # Entry 1: Parameters ($lookup)
    assert entries[1]["resource"]["resourceType"] == "Parameters"
    # Entry 2: Parameters (closure add) - 1 concept
    e2_concepts = _find_params(entries[2]["resource"], "concept")
    assert len(e2_concepts) == 1


# ===========================================================================
# Lens 8: Cross-handler content-type + resource-type parity audit.
# EXPLORER walks app.routes and confirms $closure POST emits FHIR MIME.
# ===========================================================================


def test_e80_walk_routes_closure_post_emits_fhir_json(fhir_client):
    """L8a: walk app.routes — $closure POST returns application/fhir+json.

    Mirrors Milestone-1 CR-001 probe class.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only("explorer-e80-walk"),
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/fhir+json"
    assert r.json()["resourceType"] == "Parameters"


def test_e81_walk_routes_closure_post_xml_emits_fhir_xml(fhir_client):
    """L8b: walk app.routes — $closure POST with _format=xml returns
    application/fhir+xml."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure?_format=xml",
        json=_closure_name_only("explorer-e81-walk-xml"),
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/fhir+xml"
    assert "<Parameters" in r.text


def test_e82_walk_routes_batch_closure_emits_fhir_json(fhir_client):
    """L8c: walk app.routes — batch POST /fhir returns application/fhir+json
    when entry is $closure."""
    r = fhir_client.post(
        "/fhir",
        json=_bundle_with_closure_entry("explorer-e82-batch-walk"),
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/fhir+json"
    body = r.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "batch-response"


# ===========================================================================
# Lens 9: Spec In/Out parameter shape audit.
# Per FHIR R4 $closure OperationDefinition:
#   In: name (1..1 string), concept (0..* Coding), version (0..1 string)
#   Out: return (1..1 ConceptMap — CURRENTLY DEFERRED per CF-SKEPTIC-CM03-01)
# ===========================================================================


def test_e90_in_name_required_returns_400_on_missing(fhir_client):
    """L9a: In `name` is 1..1 — missing produces 400 OperationOutcome."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json={"resourceType": "Parameters", "parameter": []},
    )
    assert r.status_code == 400, r.text
    assert r.headers["content-type"] == "application/fhir+json"
    body = r.json()
    assert body["resourceType"] == "OperationOutcome"


def test_e91_in_concept_zero_or_more_accepts_zero(fhir_client):
    """L9b: In `concept` is 0..* — zero concepts is valid (init/reset path)."""
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only("explorer-e91-zero-concepts"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 0 concepts in response
    concepts = _find_params(body, "concept")
    assert len(concepts) == 0


def test_e92_in_concept_zero_or_more_accepts_many(fhir_client):
    """L9c: In `concept` is 0..* — many concepts accepted."""
    # EC-11 QC-269: codes must resolve to an active atom — bogus X-codes
    # are rejected. Use the real fixture codes (repetition is fine: set
    # semantics keep the concept list small, and the 0..* contract is
    # exercised by SENDING 20 entries).
    real = [
        {"system": SNOMED_URI, "code": DM_CODE},
        {"system": SNOMED_URI, "code": T2DM_CODE},
        {"system": ICD10CM_URI, "code": "E11"},
        {"system": RXNORM_URI, "code": "860975"},
    ]
    concepts = [dict(real[i % len(real)]) for i in range(20)]
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts("explorer-e92-many-concepts", concepts),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Set semantics: 4 distinct (system, code) pairs retained.
    response_concepts = _find_params(body, "concept")
    assert len(response_concepts) == len(real)


def test_e93_out_return_always_present(fhir_client):
    """L9d: Out `return` is 1..1 — always present in the response."""
    # init path
    r1 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only("explorer-e93-return-init"),
    )
    assert r1.status_code == 200, r1.text
    assert _find_param(r1.json(), "return") is not None

    # add path
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(
            "explorer-e93-return-add",
            [{"system": SNOMED_URI, "code": DM_CODE, "display": "DM"}],
        ),
    )
    assert r2.status_code == 200, r2.text
    assert _find_param(r2.json(), "return") is not None


def test_e94_in_version_param_accepted_silently(fhir_client):
    """L9e: In `version` (0..1 string) — per FHIR R4 spec, this is for
    resynchronisation. The current implementation accepts but ignores it
    (carry-forward pin)."""
    # The current implementation parses only `name` via _parse_parameters;
    # `version` is silently ignored. EXPLORER documents this.
    body_with_version = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "name", "valueString": "explorer-e94-version"},
            {"name": "version", "valueString": "some-version-hash-abc"},
        ],
    }
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=body_with_version,
    )
    # The version param is silently ignored — no 4xx, no 5xx
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/fhir+json"
    body = r.json()
    assert body["resourceType"] == "Parameters"
    # return is present (version doesn't break the response shape)
    assert _find_param(body, "return") is not None


# ===========================================================================
# Lens 10: Cross-handler version hash parity.
# EXPLORER verifies the version hash is byte-exact between per-op POST
# and batch dispatcher for the same concept input (extending HISTORIAN
# test_h90 which asserts "byte-equivalent" without parametrizing over
# multiple input shapes).
# ===========================================================================


@pytest.mark.parametrize("label,concepts", [
    ("empty", []),
    ("one-concept", [{"system": SNOMED_URI, "code": DM_CODE, "display": "DM"}]),
    ("two-concepts", [
        {"system": SNOMED_URI, "code": DM_CODE, "display": "DM"},
        {"system": SNOMED_URI, "code": T2DM_CODE, "display": "T2DM"},
    ]),
    ("alias-system", [
        {"system": SNOMED_URI_OID_ALIAS, "code": DM_CODE, "display": "DM"},
    ]),
])
def test_e100_per_op_vs_batch_version_hash_byte_exact(
    fhir_client, label, concepts,
):
    """L10: per-op POST and batch dispatcher produce byte-exact version
    hash for the same input concept shape."""
    name_po = f"explorer-e100-{label}-po"
    name_batch = f"explorer-e100-{label}-batch"
    if concepts:
        r_po = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_with_concepts(name_po, concepts),
        )
        r_batch = fhir_client.post(
            "/fhir",
            json=_bundle_with_closure_entry(name_batch, concepts),
        )
    else:
        r_po = fhir_client.post(
            "/fhir/CodeSystem/$closure",
            json=_closure_name_only(name_po),
        )
        r_batch = fhir_client.post(
            "/fhir",
            json=_bundle_with_closure_entry(name_batch),
        )
    assert r_po.status_code == 200, r_po.text
    assert r_batch.status_code == 200, r_batch.text
    hash_po = _return_hash(r_po.json())
    hash_batch = _return_hash(r_batch.json()["entry"][0]["resource"])
    # Hashes are byte-exact equal (name is NOT part of payload; concept
    # set is identical)
    assert hash_po == hash_batch, (
        f"hash drift on {label}: per-op={hash_po!r} batch={hash_batch!r}"
    )


# ===========================================================================
# Lens 11: Manager-level API invariants (extends SKEPTIC + HISTORIAN
# manager probes with EXPLORER lateral combinations).
# ===========================================================================


def test_e110_manager_reset_then_get_or_create_returns_fresh_instance():
    """L11a: reset(name) -> get_or_create(name) returns the FRESH instance
    (NOT the pre-reset instance)."""
    m = ClosureManager()
    t1 = m.get_or_create("explorer-e110-reset-get")
    t1.concepts = {"X": {"system": "S", "display": "X"}}
    t_reset = m.reset("explorer-e110-reset-get")
    t_after = m.get_or_create("explorer-e110-reset-get")
    assert t_after is t_reset  # get_or_create returns the reset instance
    assert t_after is not t1  # NOT the pre-reset instance
    assert t_after.concepts == {}  # fresh state


def test_e111_manager_get_returns_none_for_unknown():
    """L11b: get(name) returns None for an unknown name."""
    m = ClosureManager()
    assert m.get("explorer-e111-never") is None


def test_e112_manager_list_names_excludes_reset_target_pre_reset():
    """L11c: list_names reflects the CURRENT state — names added but not
    yet reset appear in list_names."""
    m = ClosureManager()
    m.get_or_create("explorer-e112-listed")
    assert "explorer-e112-listed" in m.list_names()
    # After reset, the name is STILL in list_names (reset replaces, doesn't remove)
    m.reset("explorer-e112-listed")
    assert "explorer-e112-listed" in m.list_names()


def test_e113_manager_two_distinct_names_independent_state():
    """L11d: two distinct names produce independent ClosureTable instances
    with independent state."""
    m = ClosureManager()
    t_a = m.get_or_create("explorer-e113-a")
    t_b = m.get_or_create("explorer-e113-b")
    assert t_a is not t_b
    t_a.concepts = {("S", "X"): {"system": "S", "display": "X"}}
    t_b.concepts = {("S", "Y"): {"system": "S", "display": "Y"}}
    assert ("S", "X") in t_a.concepts
    assert ("S", "Y") not in t_a.concepts
    assert ("S", "Y") in t_b.concepts
    assert ("S", "X") not in t_b.concepts
    # Different version hashes
    assert t_a.version_hash() != t_b.version_hash()


# ===========================================================================
# Lens 12: Build_closure_response direct audit (extends EXPLORER test_e120
# baseline with lateral combinations).
# ===========================================================================


def test_e120_build_closure_response_empty_concepts():
    """L12a: build_closure_response on empty closure produces Parameters
    with just the `return` parameter (no concept entries)."""
    closure = ClosureTable("explorer-e120-empty")
    response = build_closure_response(closure)
    assert response["resourceType"] == "Parameters"
    params = response["parameter"]
    # EC-11 QC-267: the ``incomplete`` flag parameter is also present.
    assert len(params) == 2
    assert params[0]["name"] == "return"
    assert "valueString" in params[0]
    assert params[1]["name"] == "incomplete"


def test_e121_build_closure_response_canonicalizes_all_known_sources():
    """L12b: build_closure_response canonicalizes every known source to
    its FHIR R4 URI."""
    closure = ClosureTable("explorer-e121-canonical")
    closure.concepts = {
        ("SNOMEDCT_US", "73211009"): {"system": "SNOMEDCT_US", "display": "DM"},
        ("ICD10CM", "E11"): {"system": "ICD10CM", "display": "T2DM"},
        ("RXNORM", "860975"): {"system": "RXNORM", "display": "Metformin"},
    }
    response = build_closure_response(closure)
    concept_entries = _find_params(response, "concept")
    systems = {c["valueCoding"]["system"] for c in concept_entries}
    assert "http://snomed.info/sct" in systems
    assert "http://hl7.org/fhir/sid/icd-10-cm" in systems
    assert "http://www.nlm.nih.gov/research/umls/rxnorm" in systems


def test_e122_build_closure_response_unknown_source_passthrough():
    """L12c: build_closure_response passes through unknown source labels
    as-is (per system_to_fhir_uri contract)."""
    closure = ClosureTable("explorer-e122-unknown")
    closure.concepts = {
        ("UNKNOWN_SOURCE", "X1"): {"system": "UNKNOWN_SOURCE", "display": "Unknown"},
    }
    response = build_closure_response(closure)
    concept_entries = _find_params(response, "concept")
    # system_to_fhir_uri returns None for unknown source; the source
    # label is passed through as-is per to_parameter_list
    assert concept_entries[0]["valueCoding"]["system"] == "UNKNOWN_SOURCE"


def test_e123_build_closure_response_concept_count_matches():
    """L12d: build_closure_response concept count matches concepts dict size."""
    closure = ClosureTable("explorer-e123-count")
    for i in range(10):
        closure.concepts[("SNOMEDCT_US", f"C{i:03d}")] = {
            "system": "SNOMEDCT_US", "display": f"C{i}"}
    response = build_closure_response(closure)
    concept_entries = _find_params(response, "concept")
    assert len(concept_entries) == 10


# ===========================================================================
# Lens 13: Hostile-name batch + cross-handler XML parity combinations.
# EXPLORER combines Lens 2 (batch hostile-name) with Lens 3 (XML parity)
# for a unique lateral corner: XML output on a batch with hostile name.
# ===========================================================================


def test_e130_xml_batch_hostile_name_does_not_500(fhir_client):
    """L13a: XML batch dispatcher with hostile name does not 500."""
    hostile = "'; DROP TABLE closure; --"
    r = fhir_client.post(
        "/fhir?_format=xml",
        json=_bundle_with_closure_entry(hostile),
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/fhir+xml"
    assert "<Bundle" in r.text
    # The entry's resource is XML-serialized via resourceType
    assert 'value="Parameters"' in r.text


def test_e131_xml_batch_hostile_name_with_concepts(fhir_client):
    """L13b: XML batch with hostile name + concepts serializes correctly."""
    hostile = "<script>alert('xss')</script>"
    r = fhir_client.post(
        "/fhir?_format=xml",
        json=_bundle_with_closure_entry(hostile, [
            {"system": SNOMED_URI, "code": DM_CODE, "display": "DM"},
            {"system": SNOMED_URI, "code": T2DM_CODE, "display": "T2DM"},
        ]),
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/fhir+xml"
    body = r.text
    assert "<Bundle" in body
    assert 'value="Parameters"' in body
    assert "valueCoding" in body
    assert DM_CODE in body
    assert T2DM_CODE in body


def test_e132_xml_per_op_vs_batch_hostile_name_equivalent_shape(fhir_client):
    """L13c: XML body shape on hostile name is equivalent across per-op
    POST and batch dispatcher."""
    hostile = "name\0with\0null"
    # per-op
    r_po = fhir_client.post(
        "/fhir/CodeSystem/$closure?_format=xml",
        json=_closure_name_only(hostile),
    )
    # batch
    r_batch = fhir_client.post(
        "/fhir?_format=xml",
        json=_bundle_with_closure_entry(hostile),
    )
    assert r_po.status_code == 200, r_po.text
    assert r_batch.status_code == 200, r_batch.text
    # Both have valueString wire-format for the return parameter
    assert "valueString" in r_po.text
    assert "valueString" in r_batch.text
    # Both have Parameters (per-op uses <Parameters element; batch uses resourceType attr)
    assert "<Parameters" in r_po.text
    assert 'value="Parameters"' in r_batch.text


# ===========================================================================
# Lens 14: Carry-forward pinning for EXPLORER.
# These carry-forwards were documented by SKEPTIC + HISTORIAN; EXPLORER
# adds additional pins via the lateral lens to ensure they remain DEFERRED.
# ===========================================================================


def test_e140_cf_skeptic_cm03_02_subsumes_uses_hierarchy_not_closure_source_audit():
    """L14a: source-read — $subsumes handler does NOT consult ClosureManager.

    CF-SKEPTIC-CM03-02 (LOW — DEFERRED design discussion) pin via source
    read. The _do_subsumes handler walks hierarchy directly via
    is_descendant — does NOT call ClosureManager methods.
    """
    import medterm4ds.apps.fhir_api as api_mod
    src = inspect.getsource(api_mod.create_fhir_app)
    marker = "def _do_subsumes("
    idx = src.find(marker)
    assert idx >= 0, "_do_subsumes not found"
    next_def = src.find("\n    def ", idx + 1)
    body = src[idx:next_def] if next_def > idx else src[idx:]
    # _do_subsumes uses is_descendant (hierarchy walk)
    assert "is_descendant" in body
    # _do_subsumes does NOT reference ClosureManager or get_closure_manager
    assert "ClosureManager" not in body
    assert "get_closure_manager" not in body


def test_e141_cf_historian_cm03_02_incomplete_since_not_in_response_body(fhir_client):
    """L14b: incomplete_since flag is NOT surfaced in the HTTP response body.

    CF-HISTORIAN-CM03-02 (LOW — DEFERRED) pin via body-shape audit.
    """
    # EC-11 QC-267 CLOSED CF-HISTORIAN-CM03-02: the response now carries
    # an ``incomplete`` valueBoolean Out parameter (False on a healthy
    # closure, True when any walk has failed since reset).
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_name_only("explorer-e141-cf-hist"),
    )
    assert r.status_code == 200, r.text
    flags = _find_params(r.json(), "incomplete")
    assert flags == [{"name": "incomplete", "valueBoolean": False}]


def test_e142_cf_skeptic_cm03_01_return_value_string_format_pin(fhir_client):
    """L14c: CF-SKEPTIC-CM03-01 pin — return is valueString (NOT ConceptMap).

    Forward-looking pin: when a future enhancement chunk wires the
    spec-correct ConceptMap shape, this probe MUST be tightened.
    """
    r = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(
            "explorer-e142-cf01-pin",
            [{"system": SNOMED_URI, "code": DM_CODE, "display": "DM"}],
        ),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    ret = _find_param(body, "return")
    assert ret is not None
    # CF-SKEPTIC-CM03-01: return is valueString TODAY (spec deviation)
    assert "valueString" in ret
    # valueString is 12-char MD5 hex
    assert len(ret["valueString"]) == 12
    # NOT a ConceptMap resource
    assert "resource" not in ret
    assert "resourceType" not in ret


# ===========================================================================
# Lens 15: Combined-operations init across two names simultaneously.
# EXPLORER probes the concurrency-safe singleton manager pattern by
# initializing two closures with different names and verifying state
# isolation.
# ===========================================================================


def test_e150_two_names_independent_version_hashes(fhir_client):
    """L15a: two closures with different names have independent state."""
    name_a = "explorer-e150-name-a"
    name_b = "explorer-e150-name-b"
    # Both init
    r_a_init = fhir_client.post(
        "/fhir/CodeSystem/$closure", json=_closure_name_only(name_a),
    )
    r_b_init = fhir_client.post(
        "/fhir/CodeSystem/$closure", json=_closure_name_only(name_b),
    )
    assert r_a_init.status_code == 200
    assert r_b_init.status_code == 200
    # Add DM to A only
    r_a_add = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(name_a, [
            {"system": SNOMED_URI, "code": DM_CODE, "display": "DM"},
        ]),
    )
    assert r_a_add.status_code == 200
    # A has 1 concept, B has 0 concepts
    a_concepts = _find_params(r_a_add.json(), "concept")
    assert len(a_concepts) == 1
    # Re-fetch B
    r_b_add = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(name_b, [
            {"system": SNOMED_URI, "code": T2DM_CODE, "display": "T2DM"},
        ]),
    )
    assert r_b_add.status_code == 200
    b_concepts = _find_params(r_b_add.json(), "concept")
    assert len(b_concepts) == 1
    assert b_concepts[0]["valueCoding"]["code"] == T2DM_CODE
    # A still has DM, not T2DM
    a_concepts_now = _find_params(r_a_add.json(), "concept")
    assert all(c["valueCoding"]["code"] != T2DM_CODE for c in a_concepts_now)


def test_e151_two_names_distinct_hashes_after_distinct_adds(fhir_client):
    """L15b: two closures with different concepts produce distinct hashes."""
    name_a = "explorer-e151-distinct-a"
    name_b = "explorer-e151-distinct-b"
    r_a = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(name_a, [
            {"system": SNOMED_URI, "code": DM_CODE, "display": "DM"},
        ]),
    )
    r_b = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(name_b, [
            {"system": SNOMED_URI, "code": T2DM_CODE, "display": "T2DM"},
        ]),
    )
    hash_a = _return_hash(r_a.json())
    hash_b = _return_hash(r_b.json())
    # Hashes differ because concept sets differ (DM != T2DM)
    assert hash_a != hash_b


# ===========================================================================
# Lens 16: Closure version_id stability when same concept added twice.
# Per spec, the operation is NON-IDEMPOTENT ("This is not an idempotent
# operation"). EXPLORER verifies that adding the SAME concept twice does
# NOT produce a different hash (the dict-key semantic deduplicates).
# ===========================================================================


def test_e160_add_same_concept_twice_hash_stable(fhir_client):
    """L16a: adding the SAME concept twice produces the SAME hash on both
    responses.

    The closure's concepts dict is keyed by code; adding the same code
    twice overwrites the entry (no duplication). The version counter
    (_version) DOES advance on each call to add_concepts, so the hash
    WILL differ between the first and second add — but this is per the
    NON-IDEMPOTENT semantic noted in the spec.
    """
    name = "explorer-e160-idempotency"
    # First add
    r1 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(name, [
            {"system": SNOMED_URI, "code": DM_CODE, "display": "DM"},
        ]),
    )
    hash1 = _return_hash(r1.json())
    concepts1 = _find_params(r1.json(), "concept")
    assert len(concepts1) == 1
    # Second add (same concept)
    r2 = fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json=_closure_with_concepts(name, [
            {"system": SNOMED_URI, "code": DM_CODE, "display": "DM"},
        ]),
    )
    hash2 = _return_hash(r2.json())
    concepts2 = _find_params(r2.json(), "concept")
    # Concepts dict still has 1 entry (deduplicated)
    assert len(concepts2) == 1
    # EC-11 QC-270/QC-278: the hash is content-addressed and the call
    # counter is excluded — a redundant re-add keeps the same hash so
    # delta-protocol clients can skip work.
    assert hash1 == hash2


def test_e161_add_same_concept_set_twice_via_batch(fhir_client):
    """L16b: batch dispatcher exhibits the same NON-IDEMPOTENT semantic
    when adding the same concept set twice via batch."""
    name = "explorer-e161-batch-non-idempotent"
    concepts = [
        {"system": SNOMED_URI, "code": DM_CODE, "display": "DM"},
    ]
    # Batch entry 1: add DM
    r1 = fhir_client.post(
        "/fhir",
        json=_bundle_with_closure_entry(name, concepts),
    )
    hash1 = _return_hash(r1.json()["entry"][0]["resource"])
    # Batch entry 2: add DM again
    r2 = fhir_client.post(
        "/fhir",
        json=_bundle_with_closure_entry(name, concepts),
    )
    hash2 = _return_hash(r2.json()["entry"][0]["resource"])
    # EC-11 QC-270/QC-278: content-identical re-add keeps the same hash
    # (batch path behaves identically to the direct route).
    assert hash1 == hash2
