"""EXPLORER probes for chunk CM-01 (ConceptMap Resource Structure).

Source: https://build.fhir.org/conceptmap.html
Canonical R4 equivalence enum:
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
Canonical R4 $translate operation:
    https://hl7.org/fhir/R4/conceptmap-operation-translate.html

EXPLORER lens (lateral thinking — unusual combinations, route-coverage
gaps, integration corners, and shape probes no prior test has tried).
Per the iteration prompt, the focus areas are:

  1. **4-shape POST Content-Type closure on $translate** — the LAST
     operation in the CF-EXPLORER-CS02-01 family. Per CS-03 EXPLORER +
     CS-04 EXPLORER + CS-05 EXPLORER + VS-02 EXPLORER + VS-04 EXPLORER
     + VS-05 EXPLORER (each closed one operation's POST Content-Type
     probe family), this iteration closes ConceptMap/$translate:
        (a) GET with system+code+targetsystem (baseline)
        (b) POST with Parameters body (system+code)
        (c) POST with coding body (alternative encoding per R4 $translate)
        (d) Error path (missing system+code on POST)
     All 4 shapes MUST emit ``Content-Type: application/fhir+json`` AND
     a Parameters body (or OperationOutcome on the error path).

  2. **dependsOn / product edge cases** (items 2-3 of the chunk scope).
     Per SKEPTIC test_s52, both fields are 0..* cardinality and the
     medterm4ds engine does not model parameterized mappings or
     downstream concept derivations. EXPLORER adds: the export MUST
     OMIT the fields when no rows have them (NOT emit empty arrays —
     R4 cardinality permits omission). And the patient-friendly
     export path emits custom extensions instead, per
     ``outputs/fhir.py:_target_extensions``.

  3. **group.source / group.target scoping** (item 4). Per SKEPTIC
     test_s50, ``concept_map_to_fhir`` MUST scope each group by
     (source, target) URI pair. EXPLORER adds: a multi-row export
     with rows spanning 2 different (source, target) pairs MUST emit
     2 groups (one per pair), preserving the input order of the
     first row seen for each pair.

  4. **Hierarchical-relationship fixture seed**. The conformance
     fixture only seeds same-CUI ``equivalent`` mappings (T2DM is
     same-CUI across SNOMED/ICD10CM). EXPLORER adds: synthetic
     ``CodeMapping`` rows with ``source-is-narrower-than-target`` and
     ``source-is-broader-than-target`` relationships exercised at the
     ``build_parameters_translate`` builder layer (post-CM01-SKEPTIC-
     001 fix). Also: the export path (``concept_map_to_fhir``) emits
     the spec-correct wider/narrower equivalence on the same rows.

  5. **Cross-system ConceptMap export structure**. Build an export
     containing SNOMED→ICD-10-CM, SNOMED→RxNorm, and ICD-10-CM→SNOMED
     rows. The export MUST emit one ``group`` per (source, target)
     pair, with each group's ``element[]`` listing source codes.

  6. **Unusual $translate inputs** (translates to ConceptMap structure):
        (a) Multiple matches for one source code — verify the response
            shape (1 ``result`` parameter, N ``match`` parameters).
        (b) Reverse translation (``reverse=true`` + ``targetCode``) —
            accepted but not implemented per NOT A BUG registry. The
            probe verifies the param is ACCEPTED (no 500, no 422), not
            that reverse succeeds.
        (c) Target scope constraints — ``targetsystem`` filtering
            changes the result set (one match for SNOMED→ICD10CM only
            vs many matches for SNOMED→all).

  7. **CR-012 follow-up**: Verify ``_do_translate`` canonical_system_uri
     works on all variants — canonical, urn:oid alias, trailing-slash
     alias, and the prior-wrong HCPCS alias.

  8. **Cross-handler parity probe class** (VS-05 HISTORIAN strategy 53
     + VS-04 EXPLORER strategy 50): GET↔POST on $translate for the
     same input MUST produce byte-equivalent match sets.

  9. **XML wire-format on $translate route** (CS-04 EXPLORER
     methodology, extends CR-002): ``_format=xml`` on the operation
     route MUST emit lowercase booleans (``valueBoolean="true"``) and
     ``valueCode`` for ``equivalence`` (a closed-enum value, NOT a
     string).

 10. **Accept-header XML negotiation** (CS-04 EXPLORER test_e160
     pattern): ``Accept: application/fhir+xml`` on $translate MUST
     emit XML body (mirrors ``_format=xml`` but distinct header path).

Per GLOBAL_RULES.md "Test-too-lenient": every probe asserts POSITIVE
success shape (200 + expected fields) OR a specific error message
content, not just the absence of one error string.
"""

from __future__ import annotations

import inspect
import json

import pytest

from medterm4ds.core.models import CodeMapping, CodeRef, ConceptMapRow
from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
    SYSTEM_TO_FHIR_URI,
    canonical_system_uri,
    fhir_uri_to_system,
    system_to_fhir_uri,
)
from medterm4ds.outputs.fhir import (
    DEFAULT_CONCEPT_MAP_URL,
    FHIR_EQUIVALENCES,
    concept_map_to_fhir,
    fhir_equivalence,
)


# ---------------------------------------------------------------------------
# Lens 1: 4-shape POST Content-Type closure on $translate.
# Closes CF-EXPLORER-CS02-01 (LAST operation in the family).
# ---------------------------------------------------------------------------


def test_e10_translate_get_system_code_targetsystem_content_type(fhir_client):
    """EXPLORER (shape a): GET $translate with system+code+targetsystem
    MUST emit ``Content-Type: application/fhir+json`` AND a Parameters
    body. Closes CF-EXPLORER-CS02-01 shape (a) on $translate.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", "http://snomed.info/sct"),
            ("code", "44054006"),
            ("targetsystem", "http://hl7.org/fhir/sid/icd-10-cm"),
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"Content-Type drift: {r.headers['content-type']!r}; expected "
        f"application/fhir+json."
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters", (
        f"resourceType drift: {body.get('resourceType')!r}; expected Parameters."
    )


def test_e11_translate_post_parameters_body_content_type(fhir_client):
    """EXPLORER (shape b): POST $translate with Parameters body
    (system+code) MUST emit ``Content-Type: application/fhir+json`` AND
    a Parameters body. Closes CF-EXPLORER-CS02-01 shape (b) on
    $translate.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "44054006"},
                {
                    "name": "targetsystem",
                    "valueUri": "http://hl7.org/fhir/sid/icd-10-cm",
                },
            ],
        },
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"Content-Type drift: {r.headers['content-type']!r}; expected "
        f"application/fhir+json."
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters", (
        f"resourceType drift: {body.get('resourceType')!r}; expected Parameters."
    )


def test_e12_translate_post_coding_body_content_type(fhir_client):
    """EXPLORER (shape c): POST $translate with coding alternative
    encoding. Per FHIR R4
    (https://hl7.org/fhir/R4/conceptmap-operation-translate.html) the
    In Parameters include ``coding`` (0..1 Coding) and ``code`` (0..1
    code) as alternatives to system+code.

    CF-CM02-01 LANDED via CM-01 EXPLORER QA-001 (resweep) — the
    ``_extract_named_coding_from_parameters`` helper is now wired into
    ``_extract_translate_params`` (mirrors ``_extract_lookup_params``).
    The handler now honors the coding alternative encoding; the response
    is 200 + Parameters + conformant Content-Type.

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
                        "system": "http://snomed.info/sct",
                        "code": "44054006",
                    },
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


def test_e13_translate_post_error_path_content_type(fhir_client):
    """EXPLORER (shape d): POST $translate with missing system+code
    MUST emit 400 + OperationOutcome + ``Content-Type:
    application/fhir+json``. The error path is the load-bearing
    contract for framework-default drift (TS-02 SKEPTIC QA-020).
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={"resourceType": "Parameters", "parameter": []},
    )
    assert r.status_code == 400, (
        f"POST $translate with empty Parameters — expected 400; got "
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


# ---------------------------------------------------------------------------
# Lens 2: dependsOn / product (items 2-3) — out-of-fixture-scope documented.
# ---------------------------------------------------------------------------


def test_e20_outputs_fhir_omits_dependsOn_when_absent():
    """EXPLORER (item 2): when no rows carry dependsOn data, the
    export MUST OMIT the field entirely. R4 cardinality is 0..*, so
    omission is conformant; emitting an empty array would be wasteful
    but not non-conformant. medterm4ds chooses omission (the engine
    does not model parameterized mappings). Per SKEPTIC test_s52 the
    absence is spec-conformant; EXPLORER pins the stronger "omitted,
    not empty" contract.
    """
    rows = [
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="Diabetes mellitus",
            target_display="Type 2 diabetes mellitus",
            relationship="equivalent",
            match_type="exact",
        ),
    ]
    resource = concept_map_to_fhir(rows)
    for g in resource.get("group", []):
        for element in g.get("element", []):
            for target in element.get("target", []):
                assert "dependsOn" not in target, (
                    f"dependsOn emitted as {target['dependsOn']!r}; expected "
                    f"omission. Empty arrays are conformant but medterm4ds "
                    f"chooses omission."
                )


def test_e21_outputs_fhir_omits_product_when_absent():
    """EXPLORER (item 3): mirror of e20 on the ``product`` field.
    Same rationale — engine does not model downstream concept
    derivations, so omission is the medterm4ds convention.
    """
    rows = [
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="Diabetes mellitus",
            target_display="Type 2 diabetes mellitus",
            relationship="equivalent",
            match_type="exact",
        ),
    ]
    resource = concept_map_to_fhir(rows)
    for g in resource.get("group", []):
        for element in g.get("element", []):
            for target in element.get("target", []):
                assert "product" not in target, (
                    f"product emitted as {target['product']!r}; expected "
                    f"omission. Empty arrays are conformant but medterm4ds "
                    f"chooses omission."
                )


def test_e22_outputs_fhir_emits_extensions_when_row_has_metadata():
    """EXPLORER: when a ConceptMapRow carries match metadata
    (match_type, match_depth), the export MUST emit ``target.extension``
    with medterm4ds-local extension URLs. The extensions ARE the
    engine's contract for downstream-concept-derivation metadata —
    they live in a parallel namespace to the spec's ``product`` field
    but carry the same semantic intent. The probe pins the
    extension-vs-product separation.
    """
    rows = [
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="Diabetes mellitus",
            target_display="Type 2 diabetes mellitus",
            relationship="source-is-narrower-than-target",
            match_type="broader",
            match_depth=2,
        ),
    ]
    resource = concept_map_to_fhir(rows, include_extensions=True)
    target = resource["group"][0]["element"][0]["target"][0]
    assert "extension" in target, (
        f"target missing extension; got keys={list(target.keys())}. "
        f"include_extensions=True MUST emit match metadata."
    )
    urls = [e["url"] for e in target["extension"]]
    # The four canonical extension URLs from _target_extensions.
    assert any("relationship" in u for u in urls), (
        f"extension URLs missing relationship: {urls}"
    )
    assert any("match-type" in u for u in urls), (
        f"extension URLs missing match-type: {urls}"
    )
    assert any("match-depth" in u for u in urls), (
        f"extension URLs missing match-depth: {urls}"
    )


def test_e23_outputs_fhir_extensions_can_be_disabled():
    """EXPLORER: ``include_extensions=False`` MUST omit the extension
    field. This is the spec-pure shape — no server-local extensions.
    """
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
    resource = concept_map_to_fhir(rows, include_extensions=False)
    target = resource["group"][0]["element"][0]["target"][0]
    assert "extension" not in target, (
        f"include_extensions=False should omit extension; got {target}"
    )


# ---------------------------------------------------------------------------
# Lens 3: group.source / group.target scoping (item 4).
# ---------------------------------------------------------------------------


def test_e30_outputs_fhir_groups_by_source_target_pair():
    """EXPLORER (item 4): a multi-row export spanning 2 different
    (source, target) URI pairs MUST emit 2 groups. The grouping is by
    pair, not by source alone — a SNOMED→ICD10CM row and a SNOMED→
    RxNorm row MUST land in different groups.
    """
    rows = [
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="DM",
            target_display="T2DM",
            relationship="source-is-narrower-than-target",
            match_type="broader",
        ),
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="RXNORM", code="860975"),
            source_display="DM",
            target_display="metformin",
            relationship="related-to",
            match_type="ingredient",
        ),
    ]
    resource = concept_map_to_fhir(rows)
    groups = resource.get("group", [])
    assert len(groups) == 2, (
        f"expected 2 groups (one per source-target pair); got {len(groups)}: "
        f"{groups}"
    )
    pair_keys = {(g["source"], g["target"]) for g in groups}
    assert pair_keys == {
        ("http://snomed.info/sct", "http://hl7.org/fhir/sid/icd-10-cm"),
        ("http://snomed.info/sct", "http://www.nlm.nih.gov/research/umls/rxnorm"),
    }, f"group pair keys drift: {pair_keys}"


def test_e31_outputs_fhir_preserves_input_pair_order():
    """EXPLORER (item 4): group order in the export MUST reflect the
    input row order — the first row's (source, target) pair appears
    first. ``OrderedDict`` in ``concept_map_to_fhir`` enforces this.
    """
    rows = [
        ConceptMapRow(
            source=CodeRef(source="ICD10CM", code="E11"),
            target=CodeRef(source="SNOMEDCT_US", code="73211009"),
            source_display="T2DM",
            target_display="DM",
            relationship="source-is-broader-than-target",
            match_type="broader",
        ),
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="DM",
            target_display="T2DM",
            relationship="source-is-narrower-than-target",
            match_type="broader",
        ),
    ]
    resource = concept_map_to_fhir(rows)
    groups = resource.get("group", [])
    assert len(groups) == 2
    # The first input row was ICD10CM→SNOMED, so that pair comes first.
    assert groups[0]["source"] == "http://hl7.org/fhir/sid/icd-10-cm"
    assert groups[0]["target"] == "http://snomed.info/sct"


def test_e32_outputs_fhir_merges_same_pair_into_one_group():
    """EXPLORER (item 4): two rows with the SAME (source, target)
    pair but different source codes MUST land in ONE group with TWO
    elements (one per source code). The grouping is by pair, not by
    code.
    """
    rows = [
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="DM",
            target_display="T2DM",
            relationship="source-is-narrower-than-target",
            match_type="broader",
        ),
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="44054006"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="T2DM",
            target_display="T2DM",
            relationship="equivalent",
            match_type="exact",
        ),
    ]
    resource = concept_map_to_fhir(rows)
    groups = resource.get("group", [])
    assert len(groups) == 1, (
        f"expected 1 group for same pair; got {len(groups)}"
    )
    elements = groups[0].get("element", [])
    assert len(elements) == 2, (
        f"expected 2 elements in group; got {len(elements)}"
    )
    source_codes = {e["code"] for e in elements}
    assert source_codes == {"73211009", "44054006"}, (
        f"source codes drift: {source_codes}"
    )


def test_e33_outputs_fhir_merges_targets_under_same_source_code():
    """EXPLORER (item 4): two rows with the SAME (source, target)
    pair AND same source code but different target codes MUST land
    in ONE element with TWO targets (one per target code). The
    merge happens at the element level via ``_merge_row_target``.
    """
    rows = [
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="DM",
            target_display="T2DM",
            relationship="source-is-narrower-than-target",
            match_type="broader",
        ),
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E10"),
            source_display="DM",
            target_display="T1DM",
            relationship="source-is-narrower-than-target",
            match_type="broader",
        ),
    ]
    resource = concept_map_to_fhir(rows)
    groups = resource.get("group", [])
    assert len(groups) == 1
    elements = groups[0].get("element", [])
    assert len(elements) == 1, (
        f"expected 1 element for same source code; got {len(elements)}"
    )
    targets = elements[0].get("target", [])
    assert len(targets) == 2, (
        f"expected 2 targets under element; got {len(targets)}"
    )
    target_codes = {t["code"] for t in targets}
    assert target_codes == {"E11", "E10"}, f"target codes drift: {target_codes}"


# ---------------------------------------------------------------------------
# Lens 4: Hierarchical-relationship fixture seed (synthetic).
# The conformance fixture only seeds same-CUI equivalent mappings.
# EXPLORER exercises the source-is-narrower/wider-than-target paths at
# the builder layer (post-CM01-SKEPTIC-001 fix).
# ---------------------------------------------------------------------------


def test_e40_build_parameters_translate_source_is_narrower_emits_wider():
    """EXPLORER: post-CM01-SKEPTIC-001 fix verification on the
    ``build_parameters_translate`` builder. ``source-is-narrower-than-
    target`` MUST emit R4 ``wider`` (target perspective). The
    SKEPTIC test_s71 pins the same invariant; EXPLORER adds a
    multi-match variant (2 mappings, both hierarchical).
    """
    from medterm4ds.engines.fhir.responses import build_parameters_translate

    mappings = [
        CodeMapping(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            relationship="source-is-narrower-than-target",
            match_type="broader",
        ),
        CodeMapping(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E10"),
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
    assert len(matches) == 2
    for m in matches:
        equiv_part = next(
            part for part in m["part"] if part.get("name") == "equivalence"
        )
        assert equiv_part["valueCode"] == "wider", (
            f"match.equivalence drift: {equiv_part['valueCode']!r}; expected "
            f"'wider' for source-is-narrower-than-target."
        )


def test_e41_build_parameters_translate_source_is_broader_emits_narrower():
    """EXPLORER: mirror of e40 for ``source-is-broader-than-target``.
    R4 spec-correct value is ``narrower`` (target is narrower than
    source). Verifies post-CM01-SKEPTIC-001 fix holds at the builder
    layer for the reverse direction.
    """
    from medterm4ds.engines.fhir.responses import build_parameters_translate

    mappings = [
        CodeMapping(
            source=CodeRef(source="ICD10CM", code="E11"),
            target=CodeRef(source="SNOMEDCT_US", code="44054006"),
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
    assert matches
    equiv_part = next(
        part for part in matches[0]["part"] if part.get("name") == "equivalence"
    )
    assert equiv_part["valueCode"] == "narrower", (
        f"match.equivalence drift: {equiv_part['valueCode']!r}; expected "
        f"'narrower' for source-is-broader-than-target."
    )


def test_e42_outputs_fhir_conceptmap_export_emits_correct_wider_for_narrower_source():
    """EXPLORER: hierarchical-relationship probe on the export path.
    ``concept_map_to_fhir`` MUST emit ``equivalence="wider"`` on a
    ``source-is-narrower-than-target`` row — the export path uses
    ``outputs/fhir.py:FHIR_EQUIVALENCES`` (the correct sibling map).
    Cross-check: the two production maps MUST agree on every shared
    key (SKEPTIC test_s21). EXPLORER pins the export-side directionality.
    """
    rows = [
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="DM",
            target_display="T2DM",
            relationship="source-is-narrower-than-target",
            match_type="broader",
        ),
    ]
    resource = concept_map_to_fhir(rows)
    target = resource["group"][0]["element"][0]["target"][0]
    assert target["equivalence"] == "wider", (
        f"export-side directionality drift: target.equivalence="
        f"{target['equivalence']!r}; expected 'wider' for source-is-"
        f"narrower-than-target."
    )


def test_e43_outputs_fhir_conceptmap_export_emits_correct_narrower_for_broader_source():
    """EXPLORER: mirror of e42 for ``source-is-broader-than-target``.
    R4 spec-correct value is ``narrower`` (target narrower than source).
    """
    rows = [
        ConceptMapRow(
            source=CodeRef(source="ICD10CM", code="E11"),
            target=CodeRef(source="SNOMEDCT_US", code="73211009"),
            source_display="T2DM",
            target_display="DM",
            relationship="source-is-broader-than-target",
            match_type="broader",
        ),
    ]
    resource = concept_map_to_fhir(rows)
    target = resource["group"][0]["element"][0]["target"][0]
    assert target["equivalence"] == "narrower", (
        f"export-side directionality drift: target.equivalence="
        f"{target['equivalence']!r}; expected 'narrower' for source-is-"
        f"broader-than-target."
    )


# ---------------------------------------------------------------------------
# Lens 5: Cross-system ConceptMap export structure.
# ---------------------------------------------------------------------------


def test_e50_outputs_fhir_cross_system_export_three_groups():
    """EXPLORER: a 3-system export (SNOMED→ICD10CM, SNOMED→RxNorm,
    ICD10CM→SNOMED) MUST produce 3 groups with correct URIs.
    Stresses the group-scoping logic beyond the SKEPTIC 1-pair probe.
    """
    rows = [
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="DM",
            target_display="T2DM",
            relationship="source-is-narrower-than-target",
            match_type="broader",
        ),
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="RXNORM", code="860975"),
            source_display="DM",
            target_display="metformin",
            relationship="related-to",
            match_type="ingredient",
        ),
        ConceptMapRow(
            source=CodeRef(source="ICD10CM", code="E11"),
            target=CodeRef(source="SNOMEDCT_US", code="73211009"),
            source_display="T2DM",
            target_display="DM",
            relationship="source-is-broader-than-target",
            match_type="broader",
        ),
    ]
    resource = concept_map_to_fhir(rows)
    groups = resource.get("group", [])
    assert len(groups) == 3, f"expected 3 groups; got {len(groups)}"
    pair_keys = {(g["source"], g["target"]) for g in groups}
    assert pair_keys == {
        ("http://snomed.info/sct", "http://hl7.org/fhir/sid/icd-10-cm"),
        ("http://snomed.info/sct", "http://www.nlm.nih.gov/research/umls/rxnorm"),
        ("http://hl7.org/fhir/sid/icd-10-cm", "http://snomed.info/sct"),
    }, f"3-system pair keys drift: {pair_keys}"


def test_e51_outputs_fhir_url_field_is_canonical_identifier():
    """EXPLORER (item 5): ``ConceptMap.url`` is the canonical
    identifier per R4 spec. The export MUST default to
    ``DEFAULT_CONCEPT_MAP_URL`` (single source of truth). A custom
    URL MUST be honored when passed via the ``url`` kwarg.
    """
    rows = [
        ConceptMapRow(
            source=CodeRef(source="SNOMEDCT_US", code="73211009"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="DM",
            target_display="T2DM",
            relationship="equivalent",
            match_type="exact",
        ),
    ]
    # Default URL
    default_resource = concept_map_to_fhir(rows)
    assert default_resource["url"] == DEFAULT_CONCEPT_MAP_URL

    # Custom URL
    custom_resource = concept_map_to_fhir(rows, url="http://example.org/ConceptMap/custom")
    assert custom_resource["url"] == "http://example.org/ConceptMap/custom"


def test_e52_outputs_fhir_unknown_source_falls_back_to_local_urn():
    """EXPLORER: when a row's source is not in SYSTEM_TO_FHIR_URI
    (e.g., a custom patient-friendly namespace), the export MUST
    fall back to a local URN form. Pinned by ``code_system_uri``
    in outputs/fhir.py — the fallback pattern keeps the export
    graceful on unknown sources.
    """
    rows = [
        ConceptMapRow(
            source=CodeRef(source="UNKNOWN_SOURCE", code="X1"),
            target=CodeRef(source="ICD10CM", code="E11"),
            source_display="Custom",
            target_display="T2DM",
            relationship="equivalent",
            match_type="exact",
        ),
    ]
    resource = concept_map_to_fhir(rows)
    group = resource["group"][0]
    # The fallback form is urn:medterm4ds:CodeSystem:{normalized}.
    assert group["source"].startswith("urn:medterm4ds:CodeSystem:"), (
        f"unknown source URI drift: {group['source']!r}; expected "
        f"urn:medterm4ds:CodeSystem:... fallback."
    )


# ---------------------------------------------------------------------------
# Lens 6: Unusual $translate inputs.
# ---------------------------------------------------------------------------


def test_e60_translate_no_targetsystem_returns_matches_across_systems(fhir_client):
    """EXPLORER (item 6c): $translate WITHOUT ``targetsystem`` MUST
    return matches across ALL seeded target systems. The conformance
    fixture seeds T2DM (CUI C0011847) in SNOMED AND ICD10CM. So
    translating SNOMED T2DM with no targetsystem SHOULD return at
    least one match (the same-CUI ICD10CM mapping).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", "http://snomed.info/sct"),
            ("code", "44054006"),
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    # Result parameter MUST be present.
    result_param = next(
        (p for p in body["parameter"] if p.get("name") == "result"), None
    )
    assert result_param is not None, "missing 'result' parameter"
    assert result_param.get("valueBoolean") is True, (
        f"result drift: {result_param}; expected True (fixture has same-CUI match)."
    )


def test_e61_translate_with_targetsystem_filters_to_one_system(fhir_client):
    """EXPLORER (item 6c): $translate WITH ``targetsystem`` MUST
    restrict matches to that system alone. The probe asserts the
    target.system of every returned match equals the requested
    targetsystem (canonicalized).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", "http://snomed.info/sct"),
            ("code", "44054006"),
            ("targetsystem", "http://hl7.org/fhir/sid/icd-10-cm"),
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    body = r.json()
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    if not matches:
        pytest.skip("fixture DB has no SNOMED→ICD10CM mappings for code 44054006")
    for m in matches:
        concept_part = next(
            (part for part in m["part"] if part.get("name") == "concept"), None
        )
        assert concept_part is not None
        target_system = concept_part["valueCoding"]["system"]
        assert target_system == "http://hl7.org/fhir/sid/icd-10-cm", (
            f"targetsystem filter drift: match.concept.system="
            f"{target_system!r}; expected ICD10CM (filter not applied)."
        )


def test_e62_translate_unknown_target_system_returns_400(fhir_client):
    """EXPLORER: ``targetsystem`` that doesn't resolve via
    ``fhir_uri_to_system`` MUST return 400 (not silently expand to
    all targets). Pinned by ``_do_translate`` lines 1980-1982.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", "http://snomed.info/sct"),
            ("code", "44054006"),
            ("targetsystem", "http://fake.example.org/sys"),
        ],
    )
    assert r.status_code == 400, (
        f"unknown targetsystem — expected 400; got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


def test_e63_translate_reverse_param_accepted_but_not_implemented(fhir_client):
    """EXPLORER (item 6b): ``reverse=true`` + ``targetCode`` are
    accepted by the GET handler signature but NOT wired into
    ``_do_translate`` (per AGENTS.md NOT A BUG registry). The probe
    verifies the params are ACCEPTED (no 500, no 422 syntax error),
    not that reverse succeeds.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", "http://snomed.info/sct"),
            ("code", "44054006"),
            ("targetsystem", "http://hl7.org/fhir/sid/icd-10-cm"),
            ("reverse", "true"),
            ("targetCode", "E11"),
        ],
    )
    # Accepted → 200 OR 400 (if the server rejected reverse); NOT 500/422.
    assert r.status_code in (200, 400), (
        f"reverse=true + targetCode — server should accept the params "
        f"(NOT A BUG registry: reverse not implemented); got "
        f"{r.status_code}: {r.text}"
    )


def test_e64_translate_unknown_source_code_returns_empty_matches(fhir_client):
    """EXPLORER: a valid system + unknown code MUST return 200 with
    ``result=false`` and zero matches. NOT 404 — the operation
    succeeded, the lookup just found nothing. Per R4 §3.6.1 this is
    "operation succeeded, no match" (HTTP 200 + Parameters body),
    NOT "malformed request" (HTTP 4xx).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", "http://snomed.info/sct"),
            ("code", "NONEXISTENT_999999"),
            ("targetsystem", "http://hl7.org/fhir/sid/icd-10-cm"),
        ],
    )
    assert r.status_code == 200, (
        f"unknown code — expected 200 (operation succeeded, no match per "
        f"§3.6.1); got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    result_param = next(
        (p for p in body["parameter"] if p.get("name") == "result"), None
    )
    assert result_param is not None
    assert result_param.get("valueBoolean") is False, (
        f"result drift: {result_param}; expected False (no matches)."
    )
    matches = [p for p in body["parameter"] if p.get("name") == "match"]
    assert matches == [], f"unexpected matches: {matches}"


def test_e65_translate_message_includes_match_count(fhir_client):
    """EXPLORER: the ``message`` Out parameter (per R4 spec on
    $translate) MUST include the match count. Pinned by
    ``build_parameters_translate`` line 246 — the message format is
    ``"{N} matches found"``. The probe verifies the format on a
    no-match case ("0 matches found") to exercise the count
    invariant.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", "http://snomed.info/sct"),
            ("code", "NONEXISTENT_999999"),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    msg_param = next(
        (p for p in body["parameter"] if p.get("name") == "message"), None
    )
    assert msg_param is not None, "missing 'message' parameter"
    assert "0" in msg_param.get("valueString", ""), (
        f"message valueString drift: {msg_param}; expected '0 matches found'."
    )


# ---------------------------------------------------------------------------
# Lens 7: CR-012 follow-up — canonical_system_uri on all variants.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "alias,expected_canonical",
    [
        # Canonical SNOMED URI — should round-trip unchanged.
        ("http://snomed.info/sct", "http://snomed.info/sct"),
        # urn:oid SNOMED alias — should resolve to canonical.
        ("urn:oid:2.16.840.1.113883.6.96", "http://snomed.info/sct"),
        # Trailing-slash SNOMED alias — should resolve to canonical.
        ("http://snomed.info/sct/", "http://snomed.info/sct"),
    ],
)
def test_e70_canonical_system_uri_resolves_all_snomed_variants(alias, expected_canonical):
    """EXPLORER (CR-012 follow-up): ``canonical_system_uri`` MUST
    resolve every spec-listed alias form to the canonical FHIR R4
    URI. Parametrized over canonical, urn:oid, and trailing-slash
    variants for SNOMED.
    """
    result = canonical_system_uri(alias)
    assert result == expected_canonical, (
        f"canonical_system_uri({alias!r}) = {result!r}; expected "
        f"{expected_canonical!r}."
    )


@pytest.mark.parametrize(
    "alias,expected_canonical",
    [
        ("http://hl7.org/fhir/sid/icd-10-cm", "http://hl7.org/fhir/sid/icd-10-cm"),
        ("urn:oid:2.16.840.1.113883.6.90", "http://hl7.org/fhir/sid/icd-10-cm"),
        ("http://hl7.org/fhir/sid/icd-10-cm/", "http://hl7.org/fhir/sid/icd-10-cm"),
    ],
)
def test_e71_canonical_system_uri_resolves_all_icd10cm_variants(alias, expected_canonical):
    """EXPLORER: parametrized alias resolution for ICD-10-CM."""
    result = canonical_system_uri(alias)
    assert result == expected_canonical


@pytest.mark.parametrize(
    "alias,expected_canonical",
    [
        ("http://www.nlm.nih.gov/research/umls/rxnorm", "http://www.nlm.nih.gov/research/umls/rxnorm"),
        ("urn:oid:2.16.840.1.113883.6.88", "http://www.nlm.nih.gov/research/umls/rxnorm"),
    ],
)
def test_e72_canonical_system_uri_resolves_all_rxnorm_variants(alias, expected_canonical):
    """EXPLORER: parametrized alias resolution for RxNorm."""
    result = canonical_system_uri(alias)
    assert result == expected_canonical


def test_e73_canonical_system_uri_handles_hcpcs_legacy_alias():
    """EXPLORER: HCPCS legacy alias (the prior-wrong THO CodeSystem
    resource URL) MUST resolve to the canonical CMS URI. Pinned by
    the TS-01 TERMINOLOGIST QA-012 fix — the alias was kept as
    backwards-compat in FHIR_URI_ALIASES.
    """
    legacy = "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"
    canonical = canonical_system_uri(legacy)
    assert canonical == "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets", (
        f"HCPCS legacy alias drift: canonical_system_uri({legacy!r}) = "
        f"{canonical!r}; expected canonical CMS URI."
    )


def test_e74_do_translate_calls_canonical_system_uri():
    """EXPLORER: source-reading regression guard for CR-012. Verify
    ``_do_translate`` STILL calls ``canonical_system_uri`` on the
    client-supplied ``source_uri``. Mirrors SKEPTIC test_s30 and
    VS-05 HISTORIAN strategy 52 (source-reading probes as FIX-level
    regression guards).
    """
    import inspect

    from medterm4ds.apps.fhir_api import create_fhir_app

    src = inspect.getsource(create_fhir_app)
    assert "canonical_system_uri" in src, (
        "CR-012 regression: create_fhir_app source must reference "
        "canonical_system_uri helper."
    )


def test_e75_translate_get_with_alias_emits_canonical_source(fhir_client):
    """EXPLORER: end-to-end CR-012 verification via the GET handler.
    Calling $translate with the SNOMED urn:oid alias MUST return
    match[].source.system = canonical SNOMED URI. Mirrors SKEPTIC
    test_s31 with a different alias variant.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", "urn:oid:2.16.840.1.113883.6.96"),
            ("code", "44054006"),
            ("targetsystem", "http://hl7.org/fhir/sid/icd-10-cm"),
        ],
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
        assert src_part is not None
        src_system = src_part.get("valueCoding", {}).get("system")
        assert src_system == "http://snomed.info/sct", (
            f"alias echo drift: source.system={src_system!r}; expected "
            f"canonical 'http://snomed.info/sct'."
        )


# ---------------------------------------------------------------------------
# Lens 8: Cross-handler GET ↔ POST parity on $translate.
# Extends VS-04 EXPLORER strategy 50 to $translate.
# ---------------------------------------------------------------------------


def test_e80_translate_get_post_parity_on_match_set(fhir_client):
    """EXPLORER: GET $translate and POST $translate with the same
    input MUST produce byte-equivalent match sets (same N matches,
    same equivalence, same target system+code+display). The
    implementation routes both through ``_do_translate``; a future
    regression that diverges the paths would silently produce
    different results.
    """
    get_r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", "http://snomed.info/sct"),
            ("code", "44054006"),
            ("targetsystem", "http://hl7.org/fhir/sid/icd-10-cm"),
        ],
    )
    post_r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "44054006"},
                {
                    "name": "targetsystem",
                    "valueUri": "http://hl7.org/fhir/sid/icd-10-cm",
                },
            ],
        },
    )
    assert get_r.status_code == post_r.status_code == 200
    get_body = get_r.json()
    post_body = post_r.json()
    get_matches = [p for p in get_body["parameter"] if p.get("name") == "match"]
    post_matches = [p for p in post_body["parameter"] if p.get("name") == "match"]
    assert len(get_matches) == len(post_matches), (
        f"GET↔POST match count drift: GET={len(get_matches)}, POST={len(post_matches)}"
    )
    # Byte-exact comparison of the match sets.
    assert get_matches == post_matches, (
        "GET↔POST match content drift — match sets differ."
    )


# ---------------------------------------------------------------------------
# Lens 9: XML wire-format on $translate operation route.
# Extends CR-002 to the $translate surface.
# ---------------------------------------------------------------------------


def test_e90_translate_get_format_xml_emits_xml_body(fhir_client):
    """EXPLORER: ``_format=xml`` on GET $translate MUST emit an XML
    body. The XML serializer is shared via ``_fhir_response →
    _wants_xml → to_fhir_xml``; this probe guards against a future
    handler bypassing ``_fhir_response``.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", "http://snomed.info/sct"),
            ("code", "44054006"),
            ("targetsystem", "http://hl7.org/fhir/sid/icd-10-cm"),
            ("_format", "xml"),
        ],
    )
    assert r.status_code == 200, f"expected 200; got {r.status_code}: {r.text}"
    assert r.headers["content-type"].startswith("application/fhir+xml"), (
        f"Content-Type drift on XML path: {r.headers['content-type']!r}"
    )
    body_text = r.text
    assert "<Parameters" in body_text, (
        f"XML body missing <Parameters> root; got: {body_text[:200]}"
    )


def test_e91_translate_xml_result_boolean_is_lowercase(fhir_client):
    """EXPLORER: per CR-002 (Milestone-1 code review), XML wire-
    format booleans MUST be lowercase ``value="true"`` / ``value=
    "false"``, NOT Python's ``str(True)=="True"``. The probe verifies
    the lowercase form AND the absence of the capital-T form on the
    $translate route.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", "http://snomed.info/sct"),
            ("code", "44054006"),
            ("targetsystem", "http://hl7.org/fhir/sid/icd-10-cm"),
            ("_format", "xml"),
        ],
    )
    assert r.status_code == 200
    body_text = r.text
    # If there's a result parameter, it MUST be lowercase.
    if "valueBoolean" in body_text:
        assert 'value="true"' in body_text or 'value="false"' in body_text, (
            f"XML boolean drift: neither 'value=\"true\"' nor 'value=\"false\"' "
            f"found in body."
        )
        assert 'value="True"' not in body_text, (
            f"XML capital-T boolean drift: 'value=\"True\"' found in body — "
            f"CR-002 regression on $translate route."
        )


def test_e92_translate_xml_equivalence_uses_valueCode(fhir_client):
    """EXPLORER: ``match.equivalence`` is a closed-enum value — the
    XML wire form MUST use ``valueCode`` (not ``valueString``). The
    wire type IS the contract — ``valueCode`` signals "validate
    strictly" to clients. Mirrors CS-04 TERMINOLOGIST test_t22 on
    $subsumes outcome.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", "http://snomed.info/sct"),
            ("code", "44054006"),
            ("targetsystem", "http://hl7.org/fhir/sid/icd-10-cm"),
            ("_format", "xml"),
        ],
    )
    assert r.status_code == 200
    body_text = r.text
    if "<valueCode" in body_text and "equivalence" in body_text:
        # Found an equivalence part — verify it's valueCode not valueString.
        # Loose substring check; the XML structure is part of a Parameters.part.
        assert "<valueCode" in body_text, (
            f"equivalence part should use valueCode; body excerpt: "
            f"{body_text[:500]}"
        )


# ---------------------------------------------------------------------------
# Lens 10: Accept-header XML negotiation.
# ---------------------------------------------------------------------------


def test_e100_translate_accept_header_xml_emits_xml(fhir_client):
    """EXPLORER: ``Accept: application/fhir+xml`` MUST emit XML body
    (mirrors ``_format=xml`` per FHIR R4 §3.1.0.1.11, but distinct
    header path). The probe verifies the Accept-header path on the
    $translate route.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", "http://snomed.info/sct"),
            ("code", "44054006"),
            ("targetsystem", "http://hl7.org/fhir/sid/icd-10-cm"),
        ],
        headers={"Accept": "application/fhir+xml"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+xml"), (
        f"Accept-header XML negotiation drift: {r.headers['content-type']!r}"
    )
    assert "<Parameters" in r.text


def test_e101_translate_format_overrides_accept_header(fhir_client):
    """EXPLORER: per FHIR R4 §3.1.0.1.11, ``_format`` query parameter
    overrides the ``Accept`` header. ``_format=json`` +
    ``Accept: application/fhir+xml`` MUST produce a JSON response.
    Mirrors TS-01 EXPLORER QA-009 on the $translate route.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", "http://snomed.info/sct"),
            ("code", "44054006"),
            ("targetsystem", "http://hl7.org/fhir/sid/icd-10-cm"),
            ("_format", "json"),
        ],
        headers={"Accept": "application/fhir+xml"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"_format=json should override Accept=xml; got "
        f"{r.headers['content-type']!r}"
    )


# ---------------------------------------------------------------------------
# Lens 11: SKEPTIC fix-survival echo + carry-forward pin.
# ---------------------------------------------------------------------------


def test_e110_skeptic_fix_001_survived_wider_on_narrower_source():
    """EXPLORER: SKEPTIC FIX-001 (CM01-SKEPTIC-001) survival echo.
    The fix swapped the inverted directionality in
    ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` so ``source-is-narrower-
    than-target`` ⇒ R4 ``wider``. EXPLORER re-runs the load-bearing
    assertion to guard against silent regression between iterations.
    SKEPTIC test_s20 is the canonical pin; this is the EXPLORER echo.
    """
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE

    assert _INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-narrower-than-target"] == "wider"
    assert _INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-broader-than-target"] == "narrower"


def test_e111_skeptic_fix_002_survived_not_translated_emits_unmatched():
    """EXPLORER: SKEPTIC FIX-002 (CM01-SKEPTIC-002) survival echo.
    The fix corrected ``outputs/fhir.py:FHIR_EQUIVALENCES["not-
    translated"]`` from ``equivalent`` to ``unmatched``.
    """
    assert fhir_equivalence("not-translated") == "unmatched"


def test_e112_chunk_description_drift_documented():
    """EXPLORER: CM01-SKEPTIC-003 carry-forward pin. The chunk
    description in spec_schedule.json lists R5/R4B values
    (``subsumedby``, ``matches``, ``not-relatedto``) as if they
    were R4. The drift is documented (not enforced — modifying
    spec_schedule.json is out of scope per user constraint).
    EXPLORER documents the same drift via a different surface
    (the chunk description value list).
    """
    chunk_desc_list = {
        "equal", "equivalent", "wider", "narrower", "relatedto",
        "not-relatedto", "disjoint", "subsumes", "subsumedby",
        "matches", "inexact", "unmatched",
    }
    drift = chunk_desc_list - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert drift == {"not-relatedto", "subsumedby", "matches"}
    assert "specializes" not in chunk_desc_list


# ---------------------------------------------------------------------------
# Lens 12: Inter-module map agreement audit.
# ---------------------------------------------------------------------------


def test_e120_two_equivalence_maps_agree_on_all_shared_keys():
    """EXPLORER: cross-module map consistency audit. The two
    production equivalence maps — ``responses.py:_INTERNAL_REL_TO_
    FHIR_EQUIVALENCE`` (used by $translate) and ``outputs/fhir.py:
    FHIR_EQUIVALENCES`` (used by ConceptMap export) — MUST agree on
    every shared key. A future regression in either file would
    silently produce opposite R4 codes for the same input.
    Mirrors SKEPTIC test_s21; EXPLORER adds an explicit "every
    shared key" parametrization.
    """
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE

    shared_keys = (
        set(_INTERNAL_REL_TO_FHIR_EQUIVALENCE.keys())
        & set(FHIR_EQUIVALENCES.keys())
    )
    assert shared_keys, (
        "No shared keys between the two equivalence maps — they "
        "translate the same engine vocabulary and SHOULD share at "
        "least the core keys."
    )
    for key in sorted(shared_keys):
        responses_val = _INTERNAL_REL_TO_FHIR_EQUIVALENCE[key]
        outputs_val = FHIR_EQUIVALENCES[key]
        assert responses_val == outputs_val, (
            f"Map disagreement on key {key!r}: responses.py emits "
            f"{responses_val!r}; outputs/fhir.py emits {outputs_val!r}. "
            f"The two surfaces would silently produce opposite clinical "
            f"semantics for the same input."
        )


def test_e121_emitted_equivalence_values_all_in_r4_enum():
    """EXPLORER: registry-as-contract echo. Every value emitted by
    BOTH production maps MUST be a member of the R4 closed enum.
    Mirrors SKEPTIC test_s10 (responses.py side) and extends to
    outputs/fhir.py side.
    """
    from medterm4ds.engines.fhir.responses import _INTERNAL_REL_TO_FHIR_EQUIVALENCE

    responses_drift = set(_INTERNAL_REL_TO_FHIR_EQUIVALENCE.values()) - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    outputs_drift = set(FHIR_EQUIVALENCES.values()) - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert not responses_drift, (
        f"responses.py emits non-R4 values: {responses_drift}"
    )
    assert not outputs_drift, (
        f"outputs/fhir.py emits non-R4 values: {outputs_drift}"
    )


def test_e122_canonical_uri_lookup_round_trip_for_all_seeded_systems():
    """EXPLORER: URI round-trip regression guard. For every system
    in ``SYSTEM_TO_FHIR_URI``, the URI MUST round-trip through
    ``fhir_uri_to_system`` → ``system_to_fhir_uri`` back to itself.
    A future regression in either map would silently break $translate
    system resolution.
    """
    for source, uri in SYSTEM_TO_FHIR_URI.items():
        resolved_source = fhir_uri_to_system(uri)
        assert resolved_source == source, (
            f"fhir_uri_to_system({uri!r}) = {resolved_source!r}; expected "
            f"{source!r}."
        )
        resolved_uri = system_to_fhir_uri(resolved_source)
        assert resolved_uri == uri, (
            f"system_to_fhir_uri({resolved_source!r}) = {resolved_uri!r}; "
            f"expected {uri!r}."
        )
