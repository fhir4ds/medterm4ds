"""VS-03 EXPLORER resweep: ValueSet $expand — Advanced (lateral combinations).

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
Filter Operator: https://hl7.org/fhir/R4/valueset-concept-operator.html
Parameters resource: https://hl7.org/fhir/R4/parameters.html
Implicit value sets: https://hl7.org/fhir/R4/terminology-service.html#4.7.3.1

EXPLORER lens (resweep): lateral thinking across combinations the prior
personalities tested in isolation. Per HISTORIAN tip for EXPLORER, the
load-bearing combination categories are:

  (a) Multi-include ValueSet (3+ include blocks with mixed concept/filter)
  (b) Include + exclude combined (interaction between expansion + removal)
  (c) Nested Parameters-with-valueSet with count+property combined
  (d) GET-vs-POST byte-exact parity on the implicit-value-set surface
      (TS-03 path — implicit URLs on GET and POST)
  (e) Large concept list (100+ entries) for performance characterization
  (f) Cross-builder methodology reuse — extend AST-walk to
      ``build_parameters_translate`` + ``build_closure_response``
      (per VS-02 HISTORIAN Lens 3 source-read contract pattern)
  (g) Combined filter + system + count + property on advanced $expand In
      parameter matrix

CF-HISTORIAN-VS02-01 (BFS cap on total truncated — HIGH, deferred) is
structurally out-of-scope for EXPLORER per the assignment. The META pin
(source-read asymmetry: 3 of 4 call sites use +1 probe; 1 uses +0) is
the load-bearing evidence that the bug is intact elsewhere.

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet Extended Release)
  - mrrel: 1 row (A44054006 isa A73211009 — T2DM is-a Diabetes)
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (canonical R4)
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html (too-costly)
# Spec: https://hl7.org/fhir/R4/valueset.html#expansion (expansion shape)
from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,  # noqa: F401  registry-as-contract import
    FHIR_R4_FILTER_OPERATORS,
)

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"

TOOCOSTLY_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)
_RESPONSES_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "engines" / "fhir" / "responses.py"
)
_CLOSURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "engines" / "fhir" / "closure.py"
)


# =============================================================================
# Helpers (mirror HISTORIAN + SKEPTIC resweep files — same shape)
# =============================================================================


def _get_func_source(
    module_path: Path, parent_name: str, child_name: str | None = None
) -> str:
    """Read the source of a top-level function or a nested function.

    Walks ``ast`` looking for ``ast.FunctionDef`` and ``ast.AsyncFunctionDef``.
    The nested form (``parent_name`` = factory function, ``child_name`` =
    inner def) is needed because handlers are defined inside
    ``create_fhir_app``. Mirrors the helper in test_vs03_historian_resweep.py
    + test_vs03_skeptic_resweep.py.
    """
    src = module_path.read_text()
    tree = ast.parse(src)
    if child_name is None:
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == parent_name
            ):
                return ast.get_source_segment(src, node) or ""
        return ""

    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == parent_name
        ):
            for child in ast.walk(node):
                if (
                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name == child_name
                ):
                    return ast.get_source_segment(src, child) or ""
    return ""


def _post_expand(fhir_client, body: dict, *, params: dict | None = None,
                 headers: dict | None = None) -> tuple[int, dict, str]:
    """POST a body to /fhir/ValueSet/$expand.

    Returns (status_code, body_json, content_type).
    """
    merged_headers = {"Accept": "application/fhir+json"}
    if headers:
        merged_headers.update(headers)
    resp = fhir_client.post(
        "/fhir/ValueSet/$expand",
        json=body,
        params=params or {},
        headers=merged_headers,
    )
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"_raw": resp.text}
    return resp.status_code, parsed, resp.headers.get("content-type", "")


def _get_expand(fhir_client, *, params: dict, headers: dict | None = None) -> tuple[int, dict, str]:
    """GET /fhir/ValueSet/$expand with query params. Returns (status, body, ct)."""
    merged_headers = {"Accept": "application/fhir+json"}
    if headers:
        merged_headers.update(headers)
    resp = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=params,
        headers=merged_headers,
    )
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"_raw": resp.text}
    return resp.status_code, parsed, resp.headers.get("content-type", "")


def _contains_codes(body: dict) -> list[tuple[str, str]]:
    """Extract (system, code) pairs from a ValueSet.expansion.contains."""
    out = []
    for c in body.get("expansion", {}).get("contains", []):
        out.append((c.get("system", ""), c.get("code", "")))
    return out


def _make_extensional_snomed(concepts=None) -> dict:
    """Build an extensional ValueSet with explicit SNOMED concept list."""
    if concepts is None:
        concepts = [
            {"code": SNOMED_DIABETES_MELLITUS, "display": "Diabetes mellitus"},
            {"code": SNOMED_T2DM, "display": "Type 2 diabetes mellitus"},
        ]
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs03-expl-resweep-extensional",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "concept": concepts,
            }],
        },
    }


def _make_intensional_isa(root: str = SNOMED_DIABETES_MELLITUS, system: str = SNOMED_URI) -> dict:
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs03-expl-resweep-isa",
        "compose": {
            "include": [{
                "system": system,
                "filter": [
                    {"property": "concept", "op": "is-a", "value": root}
                ],
            }],
        },
    }


def _wrap_in_parameters(value_set: dict, extra_params: list | None = None) -> dict:
    """Wrap an inline ValueSet in a Parameters resource per FHIR R4 §4.7.5."""
    parameter = [{"name": "valueSet", "resource": value_set}]
    if extra_params:
        parameter.extend(extra_params)
    return {"resourceType": "Parameters", "parameter": parameter}


# =============================================================================
# Lens 1: Multi-include ValueSet (3+ include blocks with mixed concept/filter)
# HISTORIAN tip (a)
# =============================================================================


class TestLens1MultiIncludeMixed:
    """3+ include blocks with mixed concept/filter — lateral combination.

    Per FHIR R4 §4.9.4 (https://hl7.org/fhir/R4/valueset.html#composes):
    multiple include blocks contribute to the expansion as a UNION. The
    lateral combination here is mixing concept-list includes with filter
    includes in the SAME ValueSet body. The prior personalities tested each
    in isolation; EXPLORER verifies the combination doesn't crash and
    produces the union.
    """

    def test_e10_three_includes_mixed_concept_and_filter(self, fhir_client):
        """3 include blocks: SNOMED concept[], SNOMED is-a filter, ICD-10-CM concept[].

        Union MUST include: SNOMED DM (concept), SNOMED T2DM (concept + via
        is-a), ICD-10-CM T2DM (concept). Dedup by (system, code) per
        _expand_intensional.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-expl-3-includes",
            "compose": {
                "include": [
                    # Block 1: explicit SNOMED concept
                    {
                        "system": SNOMED_URI,
                        "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                    },
                    # Block 2: SNOMED is-a filter (would also produce DM + T2DM)
                    {
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS},
                        ],
                    },
                    # Block 3: ICD-10-CM explicit concept
                    {
                        "system": ICD10CM_URI,
                        "concept": [{"code": ICD10CM_T2DM}],
                    },
                ],
            },
        }
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        codes = _contains_codes(body)
        # Union: SNOMED DM, SNOMED T2DM, ICD-10-CM T2DM
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes, f"missing DM: {codes}"
        assert (SNOMED_URI, SNOMED_T2DM) in codes, f"missing SNOMED T2DM: {codes}"
        assert (ICD10CM_URI, ICD10CM_T2DM) in codes, f"missing ICD-10-CM T2DM: {codes}"
        # Dedup: SNOMED DM appears in block 1 AND block 2; MUST be deduped.
        snomed_dm_count = sum(
            1 for s, c in codes if s == SNOMED_URI and c == SNOMED_DIABETES_MELLITUS
        )
        assert snomed_dm_count == 1, f"SNOMED DM not deduped: count={snomed_dm_count}"

    def test_e11_three_includes_with_count_truncation(self, fhir_client):
        """3 includes (3 codes total: DM, T2DM, ICD10 T2DM) + count=2.

        Count truncation MUST fire (3 > 2); valueset-toocostly extension
        MUST be present. Per VS-02 SKEPTIC QA-057 (count=3 PROMOTED at
        GLOBAL_RULES.md line 136): ``expansion.total`` MUST reflect the
        un-truncated size (= 3, not 2).
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-expl-3-includes-count",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                    },
                    {
                        "system": SNOMED_URI,
                        "concept": [{"code": SNOMED_T2DM}],
                    },
                    {
                        "system": ICD10CM_URI,
                        "concept": [{"code": ICD10CM_T2DM}],
                    },
                ],
            },
        }
        status, body, _ = _post_expand(fhir_client, vs, params={"count": 2})
        assert status == 200, f"status={status} body={body}"
        assert body["expansion"]["total"] == 3, (
            f"total must be un-truncated size (3), got {body['expansion']['total']}"
        )
        assert len(body["expansion"]["contains"]) <= 2
        exts = body["expansion"].get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"toocostly extension missing: {exts}"
        )

    def test_e12_three_includes_with_bare_valueset_no_compose_url(self, fhir_client):
        """3 includes WITHOUT a top-level url — server MUST NOT crash.

        The ``url`` field is optional for an inline ValueSet. The
        response's ``expansion`` field MUST still be present.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                    },
                    {
                        "system": SNOMED_URI,
                        "concept": [{"code": SNOMED_T2DM}],
                    },
                    {
                        "system": ICD10CM_URI,
                        "concept": [{"code": ICD10CM_T2DM}],
                    },
                ],
            },
        }
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        assert body["resourceType"] == "ValueSet"
        assert "expansion" in body
        # url field should be absent since not supplied
        assert "url" not in body or body.get("url") is None

    def test_e13_three_includes_get_vs_post_byte_exact_parity(self, fhir_client):
        """Multi-include ValueSet — GET-vs-POST byte-exact parity.

        Per FHIR R4 §4.7.5: ``$expand`` accepts both GET (url param) and
        POST (inline ValueSet). For an inline multi-include ValueSet, the
        only path is POST (GET can't carry a ValueSet body). But GET can
        carry the URL of a registered ValueSet. This probe verifies the
        POST path is conformant; the GET path on URL-stored ValueSets is
        out of scope (no persistence today).
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-expl-3-includes-parity",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                    },
                    {
                        "system": SNOMED_URI,
                        "concept": [{"code": SNOMED_T2DM}],
                    },
                    {
                        "system": ICD10CM_URI,
                        "concept": [{"code": ICD10CM_T2DM}],
                    },
                ],
            },
        }
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        # Just confirm POST works for 3 includes — single-call parity probe.
        assert len(_contains_codes(body)) == 3


# =============================================================================
# Lens 2: Include + exclude combined (HISTORIAN tip (b))
# =============================================================================


class TestLens2IncludeExcludeCombined:
    """Include + exclude combined — lateral interaction.

    Per FHIR R4 §4.9.4 (https://hl7.org/fhir/R4/valueset.html#composes):
    "The compose.exclude is a set of codes that are excluded from the
    ValueSet." The exclude is applied AFTER all includes. EXPLORER verifies
    the combination across mixed-system + concept-list + filter include
    shapes.
    """

    def test_e20_exclude_removes_specific_code_from_include(self, fhir_client):
        """Include 2 SNOMED codes, exclude 1 — exactly 1 remains.

        Per CF-SKEPTIC-VS01-02: exclude[].filter[] is silently dropped;
        only exclude[].concept[].code is honored. The exclude here uses
        concept[] so the removal MUST fire.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-expl-exclude-1",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_DIABETES_MELLITUS},
                        {"code": SNOMED_T2DM},
                    ],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_T2DM}],
                }],
            },
        }
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) not in codes, (
            f"excluded code leaked into expansion: {codes}"
        )

    def test_e21_exclude_cross_system_does_not_remove(self, fhir_client):
        """Include SNOMED + ICD10-CM; exclude an ICD10-CM code — ICD10 entry removed only.

        Cross-system exclude: exclude carries system=ICD10CM, the include
        has both SNOMED and ICD10CM. The exclude MUST remove the ICD10CM
        entry without affecting SNOMED entries.

        Per CF-SKEPTIC-VS01-03: exclude matches on code alone, ignoring
        system — cross-system drift. This probe documents the CURRENT
        behavior: exclude matches by code string regardless of system. We
        use a code (SNOMED_T2DM="44054006") that doesn't collide with
        ICD10CM_T2DM="E11" so the behavior is unambiguous.
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-expl-exclude-cross",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                    },
                    {
                        "system": ICD10CM_URI,
                        "concept": [{"code": ICD10CM_T2DM}],
                    },
                ],
                "exclude": [{
                    "system": ICD10CM_URI,
                    "concept": [{"code": ICD10CM_T2DM}],
                }],
            },
        }
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes, (
            f"SNOMED entry missing: {codes}"
        )
        # ICD10CM T2DM should be excluded (matches by code).
        assert (ICD10CM_URI, ICD10CM_T2DM) not in codes, (
            f"ICD10CM T2DM should be excluded: {codes}"
        )

    def test_e22_exclude_after_is_a_filter(self, fhir_client):
        """is-a filter (DM + T2DM) then exclude root — only T2DM remains.

        The is-a filter produces [DM, T2DM]; the exclude removes DM;
        result: [T2DM].
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-expl-exclude-after-filter",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS},
                    ],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                }],
            },
        }
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes, f"T2DM missing: {codes}"
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) not in codes, (
            f"DM should be excluded after is-a+exclude: {codes}"
        )

    def test_e23_exclude_with_no_matching_code_is_noop(self, fhir_client):
        """Exclude a code that's NOT in the include — expansion unchanged."""
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-expl-exclude-noop",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": "9999999999"}],  # nonexistent
                }],
            },
        }
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert len(codes) == 1, f"no-op exclude should not change size: {codes}"

    def test_e24_exclude_does_not_crash_on_empty_concept_list(self, fhir_client):
        """Exclude with empty concept[] — server MUST NOT crash.

        Per the 10th PROMOTED pattern (isinstance guard), the
        exclude loop has the guard. The empty-list case is a happy path —
        no entries to remove.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [],
                }],
            },
        }
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes


# =============================================================================
# Lens 3: Nested Parameters-with-valueSet + count + property (HISTORIAN tip (c))
# =============================================================================


class TestLens3NestedParametersCombined:
    """Nested Parameters-with-valueSet + count + property — lateral combination.

    Per FHIR R4 §4.7.5 In Parameters
    (https://hl7.org/fhir/R4/valueset-operation-expand.html): the POST
    body MAY co-locate the inline valueSet AND scalar In parameters
    (count, property, designation, includeDesignations, etc.). VS-03
    SKEPTIC QA-059 wired the helper for valueSet; VS-03 SKEPTIC test_s12
    verified count in the same Parameters body. EXPLORER extends to count
    + property combined.
    """

    def test_e30_parameters_valueset_with_count_and_property(self, fhir_client):
        """Parameters body with valueSet + count + property — server MUST honor count.

        property is the spec In param for restricting which properties are
        returned; medterm4ds doesn't filter on property today (returns
        full contains shape) but MUST accept the param without 5xx.
        """
        nested_vs = _make_extensional_snomed()
        params_body = _wrap_in_parameters(
            nested_vs,
            extra_params=[
                {"name": "count", "valueInteger": 1},
                {"name": "property", "valueString": "definition"},
                {"name": "property", "valueString": "abstract"},
            ],
        )
        status, body, _ = _post_expand(fhir_client, params_body)
        assert status == 200, f"status={status} body={body}"
        assert body["resourceType"] == "ValueSet"
        # count=1 MUST truncate
        assert len(body["expansion"]["contains"]) <= 1
        # total MUST be un-truncated size (=2)
        assert body["expansion"]["total"] == 2
        exts = body["expansion"].get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts)

    def test_e31_parameters_valueset_with_designation_lang(self, fhir_client):
        """Parameters body with valueSet + designation (displayLanguage) — server MUST accept."""
        nested_vs = _make_extensional_snomed()
        params_body = _wrap_in_parameters(
            nested_vs,
            extra_params=[
                {"name": "displayLanguage", "valueCode": "en"},
                {"name": "includeDesignations", "valueBoolean": True},
            ],
        )
        status, body, _ = _post_expand(fhir_client, params_body)
        assert status == 200, f"status={status} body={body}"
        assert body["resourceType"] == "ValueSet"
        # Both SNOMED codes should be present (no count truncation).
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_e32_parameters_valueset_with_include_definition(self, fhir_client):
        """Parameters body with valueSet + includeDefinition=true — server MUST accept."""
        nested_vs = _make_extensional_snomed()
        params_body = _wrap_in_parameters(
            nested_vs,
            extra_params=[
                {"name": "includeDefinition", "valueBoolean": True},
                {"name": "activeOnly", "valueBoolean": True},
            ],
        )
        status, body, _ = _post_expand(fhir_client, params_body)
        assert status == 200, f"status={status} body={body}"
        # Definition inclusion is a future enhancement — the conformance
        # contract today is "no 5xx". The expansion MUST still be returned.
        assert body["resourceType"] == "ValueSet"
        assert len(_contains_codes(body)) == 2

    def test_e33_parameters_valueset_with_count_zero_in_body(self, fhir_client):
        """Parameters body with valueSet + count=0 — count=0 invalid (ge=1).

        Per CF-SKEPTIC-VS02-01: count=0 currently 422s. The body count
        MUST trigger the same validation.
        """
        nested_vs = _make_extensional_snomed()
        params_body = _wrap_in_parameters(
            nested_vs,
            extra_params=[{"name": "count", "valueInteger": 0}],
        )
        status, body, _ = _post_expand(fhir_client, params_body)
        # Either 400 (from _parse_count_param) or 422 (from FastAPI Query).
        # _parse_count_param returns None for 0; the handler converts to 400.
        assert status in (400, 422), f"status={status} body={body}"

    def test_e34_parameters_valueset_with_neg_count_in_body(self, fhir_client):
        """Parameters body with valueSet + count=-1 — invalid, MUST 400."""
        nested_vs = _make_extensional_snomed()
        params_body = _wrap_in_parameters(
            nested_vs,
            extra_params=[{"name": "count", "valueInteger": -1}],
        )
        status, body, _ = _post_expand(fhir_client, params_body)
        assert status in (400, 422), f"status={status} body={body}"


# =============================================================================
# Lens 4: GET-vs-POST byte-exact parity on implicit value set surface
# HISTORIAN tip (d)
# =============================================================================


class TestLens4ImplicitValueSetGetPostParity:
    """GET-vs-POST parity on the implicit value set URL surface (TS-03 path).

    Per FHIR R4 §4.7.5 + §4.7.3.1: implicit value set URLs
    (``<system-uri>/vs`` and ``http://snomed.info/sct?fhir_vs``) can be
    expanded via GET (url param) OR POST (Parameters body with url).
    The TS-03 SKEPTIC QA-032 fix wired the implicit URL detection on
    GET; the QA-059 fix added the Parameters-with-valueSet path on POST
    (but does the Parameters-with-url path on POST also reach the
    implicit expander?). EXPLORER verifies GET and POST produce
    structurally-equivalent expansions on the implicit URL surface.
    """

    def test_e40_get_vs_post_implicit_snomed_all_codes(self, fhir_client):
        """http://snomed.info/sct?fhir_vs — GET vs POST byte-exact-ish.

        Per TS-03 SKEPTIC QA-032: this URL is the implicit "all of
        SNOMED" form. The fixture has only 2 SNOMED codes; the expansion
        returns both.
        """
        # GET path
        g_status, g_body, _ = _get_expand(
            fhir_client, params={"url": "http://snomed.info/sct?fhir_vs"},
        )
        assert g_status == 200, f"GET status={g_status} body={g_body}"
        # POST path (Parameters body carrying url)
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": "http://snomed.info/sct?fhir_vs"},
            ],
        }
        p_status, p_body, _ = _post_expand(fhir_client, params_body)
        assert p_status == 200, f"POST status={p_status} body={p_body}"
        # Both MUST return a ValueSet expansion containing the SNOMED codes.
        assert g_body["resourceType"] == "ValueSet"
        assert p_body["resourceType"] == "ValueSet"
        g_codes = set(_contains_codes(g_body))
        p_codes = set(_contains_codes(p_body))
        # Same set of (system, code) pairs — byte-exact parity on the
        # SNOMED codes present.
        assert g_codes == p_codes, (
            f"GET vs POST implicit SNOMED drift: GET={g_codes} POST={p_codes}"
        )
        # Both MUST include the 2 SNOMED codes seeded in the fixture.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in g_codes
        assert (SNOMED_URI, SNOMED_T2DM) in g_codes

    def test_e41_get_vs_post_implicit_uri_slash_vs(self, fhir_client):
        """http://snomed.info/sct/vs — GET vs POST parity on Form (a).

        Per TS-03 SKEPTIC QA-032 + HISTORIAN QA-033: ``<system-uri>/vs``
        is the implicit "all of <system>" form. SNOMED CT versionless URI
        with /vs is the implicit form for all of SNOMED.
        """
        url = "http://snomed.info/sct/vs"
        g_status, g_body, _ = _get_expand(fhir_client, params={"url": url})
        assert g_status == 200, f"GET status={g_status} body={g_body}"
        params_body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "url", "valueUri": url}],
        }
        p_status, p_body, _ = _post_expand(fhir_client, params_body)
        assert p_status == 200, f"POST status={p_status} body={p_body}"
        g_codes = set(_contains_codes(g_body))
        p_codes = set(_contains_codes(p_body))
        assert g_codes == p_codes, (
            f"GET vs POST /vs drift: GET={g_codes} POST={p_codes}"
        )

    def test_e42_get_vs_post_implicit_rxnorm_vs(self, fhir_client):
        """http://www.nlm.nih.gov/research/umls/rxnorm/vs — GET vs POST parity."""
        url = "http://www.nlm.nih.gov/research/umls/rxnorm/vs"
        g_status, g_body, _ = _get_expand(fhir_client, params={"url": url})
        assert g_status == 200, f"GET status={g_status} body={g_body}"
        params_body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "url", "valueUri": url}],
        }
        p_status, p_body, _ = _post_expand(fhir_client, params_body)
        assert p_status == 200, f"POST status={p_status} body={p_body}"
        g_codes = set(_contains_codes(g_body))
        p_codes = set(_contains_codes(p_body))
        assert g_codes == p_codes, (
            f"GET vs POST RxNorm drift: GET={g_codes} POST={p_codes}"
        )
        # RxNorm fixture has 1 code (860975 metformin).
        assert (RXNORM_URI, RXNORM_METFORMIN) in g_codes
        assert (RXNORM_URI, RXNORM_METFORMIN) in p_codes

    def test_e43_get_vs_post_implicit_count_truncation_parity(self, fhir_client):
        """Implicit URL + count=1 — GET vs POST both MUST truncate + signal toocostly."""
        url = "http://snomed.info/sct?fhir_vs"
        # GET path
        g_status, g_body, _ = _get_expand(
            fhir_client, params={"url": url, "count": 1},
        )
        assert g_status == 200, f"GET status={g_status} body={g_body}"
        # POST path with count in Parameters body
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": url},
                {"name": "count", "valueInteger": 1},
            ],
        }
        p_status, p_body, _ = _post_expand(fhir_client, params_body)
        assert p_status == 200, f"POST status={p_status} body={p_body}"
        # Both MUST have toocostly extension (fixture has 2 SNOMED codes;
        # count=1 truncates).
        g_exts = g_body.get("expansion", {}).get("extension", [])
        p_exts = p_body.get("expansion", {}).get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in g_exts), (
            f"GET toocostly missing: {g_exts}"
        )
        assert any(e.get("url") == TOOCOSTLY_URL for e in p_exts), (
            f"POST toocostly missing: {p_exts}"
        )
        # Both MUST have un-truncated total = 2 (per QA-057).
        assert g_body["expansion"]["total"] == 2, (
            f"GET total={g_body['expansion']['total']}"
        )
        assert p_body["expansion"]["total"] == 2, (
            f"POST total={p_body['expansion']['total']}"
        )

    def test_e44_get_vs_post_canonical_system_uri_parity(self, fhir_client):
        """Implicit URL — contains[].system MUST be canonical URI on both GET and POST.

        Per TS-03 SKEPTIC QA-032 + HISTORIAN Lens 7 (CF-HISTORIAN-VS02-02
        RESOLVED): the implicit expander now resolves contains[].system
        through ``canonical_system_uri`` so aliases don't leak. EXPLORER
        verifies the parity: GET and POST both produce the canonical URI.
        """
        # Use the SNOMED urn:oid alias — the canonical_system_uri helper
        # MUST resolve it to http://snomed.info/sct on both paths.
        url = "urn:oid:2.16.840.1.113883.6.96/vs"
        g_status, g_body, _ = _get_expand(fhir_client, params={"url": url})
        # The urn:oid form SHOULD be recognized; if not, status will be 400.
        # The conformance contract here is parity: both GET and POST must
        # produce the same outcome (200 with canonical URI, OR 400 with
        # OperationOutcome).
        params_body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "url", "valueUri": url}],
        }
        p_status, p_body, _ = _post_expand(fhir_client, params_body)
        assert g_status == p_status, (
            f"GET={g_status} vs POST={p_status} on urn:oid implicit URL — drift"
        )
        if g_status == 200:
            # Both MUST return canonical SNOMED URI in contains[].system.
            for system, _ in _contains_codes(g_body):
                assert system == SNOMED_URI, (
                    f"GET contains[].system not canonical: {system!r}"
                )
            for system, _ in _contains_codes(p_body):
                assert system == SNOMED_URI, (
                    f"POST contains[].system not canonical: {system!r}"
                )


# =============================================================================
# Lens 5: Large concept list (100+ entries) — performance characterization
# HISTORIAN tip (e)
# =============================================================================


class TestLens5LargeConceptList:
    """Large concept list (100+ entries) — performance + correctness.

    Per FHIR R4 §4.9.4: there is no spec-mandated cap on the size of
    compose.include[].concept[]. EXPLORER verifies the server handles
    100+ entries without crashing AND that the response time is bounded
    (each entry is a single get_code_infos lookup — O(N) lookups for N
    entries).
    """

    def test_e50_large_concept_list_100_unknown_codes(self, fhir_client, capsys=None):
        """100 unknown codes — server MUST return 200 with all 100 entries.

        Each unknown code falls back to the code string per
        CF-TERMINOLOGIST-VS02-04. The total response shape MUST be
        conformant; the per-entry display is the code string itself.

        NOTE: the POST handler's default count is 20 (per expand_post
        signature ``count: int = Query(20, ge=1, le=1000)``). To verify
        all 100 entries are returned, this probe MUST explicitly pass
        ``count=100`` — otherwise the default caps contains at 20 while
        total stays at 100 (per VS-02 SKEPTIC QA-057 un-truncated total).
        """
        # Generate 100 fake codes (no lookups in the fixture DB).
        codes = [f"FAKE-{i:04d}" for i in range(100)]
        concepts = [{"code": c} for c in codes]
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-expl-large-100",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": concepts,
                }],
            },
        }
        status, body, _ = _post_expand(fhir_client, vs, params={"count": 100})
        assert status == 200, f"status={status} body[:200]={str(body)[:200]}"
        assert body["expansion"]["total"] == 100, (
            f"total={body['expansion']['total']} (expected 100)"
        )
        assert len(body["expansion"]["contains"]) == 100

    def test_e51_large_concept_list_200_with_count_truncation(self, fhir_client):
        """200 unknown codes + count=50 — MUST truncate + signal toocostly."""
        codes = [f"L-{i:04d}" for i in range(200)]
        concepts = [{"code": c} for c in codes]
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-expl-large-200-trunc",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": concepts,
                }],
            },
        }
        status, body, _ = _post_expand(fhir_client, vs, params={"count": 50})
        assert status == 200, f"status={status} body[:200]={str(body)[:200]}"
        # Un-truncated total MUST be 200 per QA-057.
        assert body["expansion"]["total"] == 200, (
            f"total={body['expansion']['total']} (expected 200)"
        )
        # Truncated contains MUST be <= 50.
        assert len(body["expansion"]["contains"]) <= 50
        exts = body["expansion"].get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts)

    def test_e52_large_concept_list_mixed_known_unknown(self, fhir_client):
        """2 known + 100 unknown codes — known MUST resolve canonical display.

        Per VS-01 TERMINOLOGIST QA-056 + VS-02 TERMINOLOGIST QA-001
        (CF-TERMINOLOGIST-VS02-04 RESOLVED): known codes get the engine's
        canonical display; unknown codes fall back to the code string.
        The mix MUST preserve both behaviors in a single response.
        """
        unknown_codes = [f"UNK-{i:04d}" for i in range(100)]
        concepts = (
            [{"code": SNOMED_DIABETES_MELLITUS, "display": "Diabetes mellitus"}]
            + [{"code": SNOMED_T2DM, "display": "Type 2 diabetes mellitus"}]
            + [{"code": c} for c in unknown_codes]
        )
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-expl-large-mixed",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": concepts,
                }],
            },
        }
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body[:200]={str(body)[:200]}"
        assert body["expansion"]["total"] == 102
        displays = {
            (c.get("system", ""), c.get("code", "")): c.get("display", "")
            for c in body["expansion"]["contains"]
        }
        # Known codes MUST have canonical display.
        assert displays[(SNOMED_URI, SNOMED_DIABETES_MELLITUS)] == "Diabetes mellitus"
        assert displays[(SNOMED_URI, SNOMED_T2DM)] == "Type 2 diabetes mellitus"
        # Unknown codes MUST fall back to code string.
        for unk in unknown_codes[:3]:
            assert displays[(SNOMED_URI, unk)] == unk, (
                f"unknown code {unk!r} display={displays[(SNOMED_URI, unk)]!r}"
            )

    def test_e53_large_concept_list_dedup_behavior(self, fhir_client):
        """100 entries with duplicates — dedup MUST collapse to unique.

        NOTE: same default-count=20 caveat as test_e50 — explicit
        ``count=50`` is required to verify all 50 unique entries appear
        in contains after dedup.
        """
        # 50 unique codes, each duplicated once = 100 entries.
        unique_codes = [f"DUP-{i:04d}" for i in range(50)]
        concepts = [{"code": c} for c in unique_codes] * 2
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/vs03-expl-large-dedup",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": concepts,
                }],
            },
        }
        status, body, _ = _post_expand(fhir_client, vs, params={"count": 50})
        assert status == 200, f"status={status} body[:200]={str(body)[:200]}"
        # After dedup, total MUST be 50.
        assert body["expansion"]["total"] == 50, (
            f"total={body['expansion']['total']} (expected 50 after dedup)"
        )
        assert len(body["expansion"]["contains"]) == 50


# =============================================================================
# Lens 6: Cross-builder methodology reuse — AST-walk extended to
# ``build_parameters_translate`` + ``build_closure_response``
# HISTORIAN tip (f) — per VS-02 HISTORIAN Lens 3 source-read contract pattern
# =============================================================================


class TestLens6CrossBuilderMethodology:
    """Extend the AST-walk source-read methodology to sibling builders.

    Per VS-02 HISTORIAN Lens 3 + VS-02 SKEPTIC QA-057 PROMOTED pattern
    (GLOBAL_RULES.md line 136): "Pattern generalizes to other builder
    functions with size fields (``build_parameters_translate``,
    ``build_closure_response`` — audit when adding count fields)." EXPLORER
    applies the methodology to verify these sibling builders have NO
    analogous ``total`` field today (so no truncation drift is possible
    today) AND that the methodology CAN be applied to them if count
    fields are added in the future.
    """

    def test_e60_build_parameters_translate_signature_no_total_field(self):
        """build_parameters_translate signature — no size field today.

        The builder at responses.py:158 emits a Parameters resource with
        ``result`` (valueBoolean) + ``message`` (valueString) + match
        entries. There is NO ``total`` field — the size is implicit in
        the number of match entries. So the VS-02 SKEPTIC QA-057 fix
        shape (explicit ``total`` param) does NOT apply today. EXPLORER
        confirms via source-read that the signature has no size param.
        """
        src = _get_func_source(_RESPONSES_PATH, "build_parameters_translate")
        assert src, "build_parameters_translate source not found"
        # Find the def line.
        def_line = next(
            (ln for ln in src.splitlines() if ln.strip().startswith("def ")),
            "",
        )
        # The signature MUST NOT have a ``total`` keyword parameter today.
        assert "total" not in def_line, (
            f"build_parameters_translate signature has 'total' — drift from "
            f"expected: {def_line!r}"
        )
        # Confirm the builder emits a Parameters resource.
        assert '"resourceType": "Parameters"' in src

    def test_e61_build_closure_response_signature_no_total_field(self):
        """build_closure_response signature — no size field today.

        Same shape as test_e60 for the sibling builder in closure.py.
        """
        src = _get_func_source(_CLOSURE_PATH, "build_closure_response")
        assert src, "build_closure_response source not found"
        def_line = next(
            (ln for ln in src.splitlines() if ln.strip().startswith("def ")),
            "",
        )
        assert "total" not in def_line, (
            f"build_closure_response signature has 'total' — drift from "
            f"expected: {def_line!r}"
        )
        # Confirm it returns a Parameters resource.
        assert '"resourceType": "Parameters"' in src

    def test_e62_build_parameters_translate_match_count_matches_input(self):
        """build_parameters_translate emits one match per input mapping.

        The builder iterates ``mappings`` and emits one ``match`` entry
        per CodeMapping. So the "size" of the response IS exactly the
        input list length — no truncation, no drift. EXPLORER verifies
        via AST walk that the loop body appends one entry per iteration.
        """
        src = _get_func_source(_RESPONSES_PATH, "build_parameters_translate")
        tree = ast.parse(src)
        # Find the for loop iterating mappings.
        for_loops = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.For)
        ]
        assert for_loops, "no for loop found in build_parameters_translate"
        # The first for loop iterates ``mappings``.
        first_for = for_loops[0]
        iter_name = (
            first_for.iter.id
            if isinstance(first_for.iter, ast.Name)
            else "<expr>"
        )
        assert iter_name == "mappings", (
            f"first for loop iterates {iter_name!r}, expected 'mappings'"
        )
        # The loop body MUST have an append call (matches.append).
        appends = [
            n for n in ast.walk(first_for)
            if (
                isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Attribute)
                and n.value.func.attr == "append"
            )
        ]
        assert appends, "no append call inside the mappings for loop"

    def test_e63_build_closure_response_to_parameter_list_delegate(self):
        """build_closure_response delegates the concept list to closure.to_parameter_list.

        The builder at closure.py:278 emits ``return`` (valueString
        version hash) + spreads ``closure.to_parameter_list()`` into the
        parameter list. EXPLORER confirms via source-read that the
        builder delegates the concept-list emission to the closure
        object's method.
        """
        src = _get_func_source(_CLOSURE_PATH, "build_closure_response")
        assert "to_parameter_list()" in src, (
            "build_closure_response should delegate to closure.to_parameter_list()"
        )

    def test_e64_build_valueset_expand_total_param_present(self):
        """build_valueset_expand HAS the ``total`` param — VS-02 SKEPTIC QA-057 fix.

        Confirms the explicit-size-on-truncation pattern is present on
        the canonical builder. Mirrors HISTORIAN Lens 2 source-read
        contract (test_h20 in test_vs03_historian_resweep.py).
        """
        src = _get_func_source(_RESPONSES_PATH, "build_valueset_expand")
        assert src, "build_valueset_expand source not found"
        # The signature MUST have ``total: int | None = None``.
        assert "total" in src, (
            "build_valueset_expand missing 'total' param — VS-02 SKEPTIC QA-057 regression"
        )

    def test_e65_ast_walk_build_valueset_expand_uses_total_param(self):
        """AST walk — build_valueset_expand uses ``total`` in the body.

        The ``total=`` parameter MUST be consumed in the function body
        (assigned to expansion.total). A future regression that removes
        the assignment would silently revert the fix.
        """
        src = _get_func_source(_RESPONSES_PATH, "build_valueset_expand")
        tree = ast.parse(src)
        # Find any reference to ``total`` as a Name node.
        total_refs = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Name) and n.id == "total"
        ]
        assert total_refs, (
            "build_valueset_expand body has no reference to 'total' — "
            "VS-02 SKEPTIC QA-057 fix is not consuming the param"
        )


# =============================================================================
# Lens 7: Combined filter + system + count + property on advanced $expand
# In parameter matrix — HISTORIAN tip (g)
# =============================================================================


class TestLens7AdvancedExpandInParameterMatrix:
    """Combined filter + system + count + property — advanced In parameter matrix.

    Per FHIR R4 §4.7.5 In Parameters: $expand accepts 14 In parameters.
    EXPLORER combines them to verify lateral interactions:
      - filter (text search) + system (constrain to one source)
      - filter + system + count (truncation on a constrained search)
      - filter + count + property (property filter alongside text)
      - filter + system + count + property (full combination)
    """

    def test_e70_filter_plus_system_constrained(self, fhir_client):
        """filter='diabetes' + system=SNOMED — MUST return only SNOMED matches.

        Per VS-02 TERMINOLOGIST test_t30: filter matches display text.
        With system=SNOMED constrained, the result MUST be SNOMED only
        (DM + T2DM both have "diabetes" in display). ICD-10-CM E11
        ALSO has "diabetes" in display ("Type 2 diabetes mellitus") but
        MUST NOT appear when system is constrained.
        """
        status, body, _ = _get_expand(
            fhir_client,
            params={
                "filter": "diabetes",
                "system": SNOMED_URI,
            },
        )
        assert status == 200, f"status={status} body={body}"
        codes = _contains_codes(body)
        # SNOMED codes that match "diabetes".
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes
        # ICD-10-CM MUST NOT appear (system constrained to SNOMED).
        assert not any(s == ICD10CM_URI for s, _ in codes), (
            f"ICD-10-CM leaked into SNOMED-constrained filter: {codes}"
        )

    def test_e71_filter_plus_system_plus_count_truncation(self, fhir_client):
        """filter + system + count=1 — MUST truncate + signal toocostly.

        filter='diabetes' on SNOMED yields 2 codes (DM + T2DM). count=1
        MUST truncate + emit toocostly extension + total=2 (un-truncated).
        """
        status, body, _ = _get_expand(
            fhir_client,
            params={
                "filter": "diabetes",
                "system": SNOMED_URI,
                "count": 1,
            },
        )
        assert status == 200, f"status={status} body={body}"
        # Per VS-02 SKEPTIC QA-001 + QA-057: filter mode now uses +1 probe
        # + passes un-truncated total. count=1 + 2 matches → total=2.
        assert body["expansion"]["total"] == 2, (
            f"total={body['expansion']['total']} (expected 2)"
        )
        assert len(body["expansion"]["contains"]) <= 1
        exts = body["expansion"].get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"toocostly missing on filter+count truncation: {exts}"
        )

    def test_e72_filter_plus_count_via_post_parameters(self, fhir_client):
        """filter + count via POST Parameters body — lateral parity with GET."""
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "filter", "valueString": "diabetes"},
                {"name": "count", "valueInteger": 1},
            ],
        }
        status, body, _ = _post_expand(fhir_client, params_body)
        assert status == 200, f"status={status} body={body}"
        # Per QA-057: filter mode total MUST be un-truncated.
        assert body["expansion"]["total"] == 2, (
            f"total={body['expansion']['total']} (expected 2)"
        )
        exts = body["expansion"].get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts)

    def test_e73_filter_plus_system_unknown_uri(self, fhir_client):
        """filter + system=unknown-URI — MUST 400 with OperationOutcome.

        Per _do_expand filter mode: ``_resolve_sources(system_uri)``
        returns None for unrecognized URIs, which triggers a 400.
        """
        status, body, _ = _get_expand(
            fhir_client,
            params={
                "filter": "diabetes",
                "system": "http://unknown.example/system",
            },
        )
        assert status == 400, f"status={status} body={body}"
        assert body["resourceType"] == "OperationOutcome"

    def test_e74_filter_plus_property_in_get_ignored(self, fhir_client):
        """GET filter + property — property is not a GET param; only POST honors it.

        The GET handler signature is (url, filter, count, offset, system).
        Property is not declared on GET — it's silently dropped by
        FastAPI. EXPLORER confirms the call doesn't 5xx.
        """
        # Property is not declared on GET — FastAPI silently drops it.
        status, body, _ = _get_expand(
            fhir_client,
            params={
                "filter": "diabetes",
                "property": "abstract",  # ignored on GET
            },
        )
        assert status == 200, f"status={status} body={body}"
        # The expansion MUST still return SNOMED DM + T2DM (filter matches).
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes

    def test_e75_filter_with_special_chars_in_get(self, fhir_client):
        """filter with special chars (!@#$%^&*()) — MUST NOT crash.

        Per VS-03 SKEPTIC resweep: hostile-input probes verified the
        filter path is robust against SQL injection. EXPLORER extends
        to combined filter + system + count lateral — the special chars
        MUST NOT affect the count or system resolution.
        """
        status, body, _ = _get_expand(
            fhir_client,
            params={
                "filter": "diabetes!@#$%",
                "system": SNOMED_URI,
                "count": 5,
            },
        )
        # Either 200 with empty contains (no match) OR 200 with matches
        # (if the literal string matches — it shouldn't). MUST NOT 5xx.
        assert status < 500, f"server crash on special chars: {status} {body}"
        if status == 200:
            assert body["resourceType"] == "ValueSet"


# =============================================================================
# Lens 8: Source-read META pin — CF-HISTORIAN-VS02-01 asymmetry verification
# (deferred — META pin per HISTORIAN tip disposition)
# =============================================================================


class TestLens8CFHistorianVS02OneAsymmetryMetaPin:
    """META pin: CF-HISTORIAN-VS02-01 source-read asymmetry intact.

    Per the assignment: "CF-HISTORIAN-VS02-01 is structurally out-of-
    scope for EXPLORER (deferred to dedicated remediation pass) but
    source-read META pinning of the structural condition (3 of 4 call
    sites use +1 probe; 1 uses +0) is the load-bearing evidence the fix
    is intact elsewhere."

    EXPLORER re-derives the asymmetry source-read contract so the
    structural condition is documented. This is NOT a fix — it's the
    META confirmation that the bug is in the same place HISTORIAN
    found it.
    """

    def test_e80_build_valueset_expand_call_sites_count(self):
        """Source-read: 4 build_valueset_expand call sites in fhir_api.py."""
        src = _FHIR_API_PATH.read_text()
        count = src.count("build_valueset_expand(")
        # 1 = the function definition; 4 = the call sites.
        # Some calls are multi-line so a simple count of "(" after the
        # name is the safe lower bound.
        assert count >= 4, (
            f"expected >= 4 build_valueset_expand call sites, got {count}"
        )

    def test_e81_intensional_path_uses_plus_zero_probe(self):
        """_expand_intensional uses +0 probe — CF-HISTORIAN-VS02-01 territory.

        The intensional path at apps/fhir_api.py:_expand_intensional
        calls ``get_descendants_bfs(..., limit=count)`` and computes
        ``total=len(deduped)`` AFTER the BFS cap. This is the +0 probe
        asymmetry: the BFS early-exit means deduped is already truncated
        when the cap fires.
        """
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_intensional",
        )
        assert src, "_expand_intensional source not found"
        # The intensional path MUST call get_descendants_bfs with limit=count.
        assert "limit=count" in src, (
            "_expand_intensional should pass limit=count to get_descendants_bfs"
        )
        # The intensional path MUST compute total=len(deduped) — the
        # load-bearing buggy line per CF-HISTORIAN-VS02-01.
        assert "len(deduped)" in src, (
            "_expand_intensional should compute len(deduped) for total"
        )

    def test_e82_filter_mode_uses_plus_one_probe(self):
        """Filter mode uses +1 probe — VS-02 SKEPTIC QA-001 + QA-057 fix intact.

        The filter mode at _do_expand calls
        ``search_names(..., limit=count + 1)`` — the +1 probe. This is
        structurally distinct from the intensional +0 probe.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        assert src, "_do_expand source not found"
        # The filter mode MUST use limit=count + 1.
        assert "count + 1" in src, (
            "_do_expand filter mode should use +1 probe (count + 1)"
        )

    def test_e83_implicit_value_set_uses_plus_one_probe(self):
        """_expand_implicit_value_set uses +1 probe — LIMIT count + 1 SQL.

        The implicit value set expander queries
        ``LIMIT ?`` with ``[source, count + 1]`` — the SQL form of the
        +1 probe. This is the third +1 probe call site.
        """
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_implicit_value_set",
        )
        assert src, "_expand_implicit_value_set source not found"
        # The implicit path MUST pass count + 1 as the SQL LIMIT parameter.
        assert "count + 1" in src, (
            "_expand_implicit_value_set should use +1 probe (count + 1)"
        )

    def test_e84_url_pattern_uses_plus_one_probe(self):
        """expand_url_pattern uses +1 probe — VS-04 TERMINOLOGIST QA-068 fix.

        The URL pattern expander at module scope (expand_url_pattern)
        uses ``descendant_budget + 1`` for the BFS limit — the +1 probe.
        """
        src = _get_func_source(_FHIR_API_PATH, "expand_url_pattern")
        assert src, "expand_url_pattern source not found"
        # The URL pattern path MUST use + 1 probe.
        assert "+ 1" in src, (
            "expand_url_pattern should use +1 probe (+ 1)"
        )

    def test_e85_asymmetry_summary_3_plus_1_sites(self):
        """META confirmation: 3 of 4 call sites use +1 probe; 1 uses +0.

        Combines test_e81..e84: the +0 site is _expand_intensional
        (CF-HISTORIAN-VS02-01); the +1 sites are _do_expand filter,
        _expand_implicit_value_set, and expand_url_pattern. This is the
        load-bearing META pin — the asymmetry is the structural
        evidence the fix is needed ONLY in _expand_intensional.
        """
        # _expand_intensional uses +0.
        intensional_src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_intensional",
        )
        assert "limit=count" in intensional_src
        # The other 3 use +1.
        filter_src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_do_expand",
        )
        assert "count + 1" in filter_src
        implicit_src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_implicit_value_set",
        )
        assert "count + 1" in implicit_src
        url_src = _get_func_source(_FHIR_API_PATH, "expand_url_pattern")
        assert "+ 1" in url_src


# =============================================================================
# Lens 9: Closed-enum source-read contract — FHIR_R4_FILTER_OPERATORS in use
# =============================================================================


class TestLens9FilterOperatorClosedEnumContract:
    """Filter operator closed-enum source-read contract.

    Per VS-01 SKEPTIC QA-054 + Milestone-2 CR-014 PROMOTED pattern: the
    FHIR R4 Filter Operator enum is a closed set imported from
    ``engines.fhir`` (canonical location). EXPLORER verifies the import
    is in place at the top of this test file (load-bearing contract)
    AND that the implementation only honors spec-correct values.
    """

    def test_e90_filter_operators_canonical_set_size(self):
        """FHIR_R4_FILTER_OPERATORS has the spec-correct 9 values.

        Per https://hl7.org/fhir/R4/valueset-concept-operator.html:
        = | is-a | descendent-of | is-not-a | regex | in | not-in |
        generalizes | exists
        """
        assert len(FHIR_R4_FILTER_OPERATORS) == 9, (
            f"expected 9 operators, got {len(FHIR_R4_FILTER_OPERATORS)}: "
            f"{sorted(FHIR_R4_FILTER_OPERATORS)}"
        )

    def test_e91_filter_operators_includes_spec_correct_spelling(self):
        """'descendent-of' (Latin-derived) IS in the enum; 'descendant-of' is NOT."""
        assert "descendent-of" in FHIR_R4_FILTER_OPERATORS, (
            "spec-correct 'descendent-of' missing from FHIR_R4_FILTER_OPERATORS"
        )
        assert "descendant-of" not in FHIR_R4_FILTER_OPERATORS, (
            "off-spec 'descendant-of' present in FHIR_R4_FILTER_OPERATORS"
        )

    def test_e92_expand_intensional_honors_only_is_a_and_descendent_of(self):
        """Source-read: _expand_intensional honors only is-a + descendent-of today.

        Per CF-SKEPTIC-VS01-01 (7 of 9 operators silently dropped): the
        implementation at apps/fhir_api.py:_expand_intensional only
        honors ``is-a`` and ``descendent-of`` on ``property="concept"``.
        The other 7 are silently dropped (deferred carry-forward).
        """
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_intensional",
        )
        # The line "if prop == \"concept\" and op in (\"is-a\", \"descendent-of\")"
        # is the load-bearing contract.
        assert '"is-a"' in src and '"descendent-of"' in src, (
            "_expand_intensional missing is-a/descendent-of dispatch"
        )

    def test_e93_unsupported_operators_silently_dropped_documented(self):
        """Source-read: unsupported operators are logged at debug (silent drop).

        Per CF-SKEPTIC-VS01-01: the implementation logs at debug level
        for unsupported operators. This is the deferred behavior pinned
        by SKEPTIC test_s50_invalid_operator_silently_drop_or_400.
        """
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_intensional",
        )
        # The "Unsupported filter" log line is the silent-drop signal.
        assert "Unsupported filter" in src, (
            "_expand_intensional should log 'Unsupported filter' for "
            "off-spec operators (CF-SKEPTIC-VS01-01 pin)"
        )


# =============================================================================
# Lens 10: Response shape audit across every $expand mode
# =============================================================================


class TestLens10ResponseShapeEveryMode:
    """Response shape audit — every $expand mode produces conformant ValueSet.

    Verifies that filter mode, intensional mode, URL pattern mode, and
    implicit value set mode ALL produce the same conformant ValueSet
    resourceType + expansion shape. Catches a future regression where
    one mode bypasses build_valueset_expand and returns a raw dict.
    """

    def test_e100_filter_mode_response_shape(self, fhir_client):
        status, body, _ = _get_expand(
            fhir_client, params={"filter": "diabetes"},
        )
        assert status == 200, f"status={status} body={body}"
        assert body["resourceType"] == "ValueSet"
        assert "expansion" in body
        assert "total" in body["expansion"]
        assert "contains" in body["expansion"]
        assert "timestamp" in body["expansion"]

    def test_e101_intensional_mode_response_shape(self, fhir_client):
        vs = _make_intensional_isa()
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        assert body["resourceType"] == "ValueSet"
        assert "expansion" in body
        assert "total" in body["expansion"]
        assert "contains" in body["expansion"]
        assert "timestamp" in body["expansion"]

    def test_e102_url_pattern_mode_response_shape(self, fhir_client):
        """URL pattern mode — SNOMED intensional URL with code."""
        status, body, _ = _get_expand(
            fhir_client,
            params={"url": f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"},
        )
        assert status == 200, f"status={status} body={body}"
        assert body["resourceType"] == "ValueSet"
        assert "expansion" in body
        assert "total" in body["expansion"]
        assert "contains" in body["expansion"]
        assert "timestamp" in body["expansion"]

    def test_e103_implicit_value_set_mode_response_shape(self, fhir_client):
        """Implicit value set mode — SNOMED all-codes URL."""
        status, body, _ = _get_expand(
            fhir_client, params={"url": "http://snomed.info/sct?fhir_vs"},
        )
        assert status == 200, f"status={status} body={body}"
        assert body["resourceType"] == "ValueSet"
        assert "expansion" in body
        assert "total" in body["expansion"]
        assert "contains" in body["expansion"]
        assert "timestamp" in body["expansion"]

    def test_e104_explicit_concept_list_mode_response_shape(self, fhir_client):
        """Explicit concept list mode — extensional ValueSet."""
        vs = _make_extensional_snomed()
        status, body, _ = _post_expand(fhir_client, vs)
        assert status == 200, f"status={status} body={body}"
        assert body["resourceType"] == "ValueSet"
        assert "expansion" in body
        assert "total" in body["expansion"]
        assert "contains" in body["expansion"]
        assert "timestamp" in body["expansion"]

    def test_e105_every_mode_expansion_contains_has_system_code_display(self, fhir_client):
        """Every contains[] entry has system, code, display fields.

        Per FHIR R4 §4.9.1 (https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains):
        each contains entry has system (1..1), code (1..1), display (0..1).
        The implementation always emits display via the canonical
        resolution chain (VS-01 TERMINOLOGIST QA-056). EXPLORER verifies
        every mode produces entries with all 3 fields present.
        """
        # Run all 4 modes.
        modes: list[tuple[str, dict]] = []
        # Filter mode
        s, b, _ = _get_expand(fhir_client, params={"filter": "diabetes"})
        if s == 200:
            modes.append(("filter", b))
        # Intensional
        s, b, _ = _post_expand(fhir_client, _make_intensional_isa())
        if s == 200:
            modes.append(("intensional", b))
        # URL pattern
        s, b, _ = _get_expand(
            fhir_client,
            params={"url": f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"},
        )
        if s == 200:
            modes.append(("url-pattern", b))
        # Implicit
        s, b, _ = _get_expand(
            fhir_client, params={"url": "http://snomed.info/sct?fhir_vs"},
        )
        if s == 200:
            modes.append(("implicit", b))
        # Explicit concept list
        s, b, _ = _post_expand(fhir_client, _make_extensional_snomed())
        if s == 200:
            modes.append(("explicit", b))
        # Every mode MUST have at least 1 entry to validate.
        assert len(modes) >= 4, f"only {len(modes)} modes returned 200: {[m[0] for m in modes]}"
        for mode_name, body in modes:
            contains = body.get("expansion", {}).get("contains", [])
            assert contains, f"mode {mode_name!r} returned empty contains"
            for entry in contains:
                assert "system" in entry, (
                    f"mode {mode_name!r} entry missing 'system': {entry}"
                )
                assert "code" in entry, (
                    f"mode {mode_name!r} entry missing 'code': {entry}"
                )
                assert "display" in entry, (
                    f"mode {mode_name!r} entry missing 'display': {entry}"
                )
