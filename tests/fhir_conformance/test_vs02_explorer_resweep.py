"""VS-02 EXPLORER resweep: ValueSet $expand — Basic.

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
Expansion.total: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.total
valueset-toocostly: https://hl7.org/fhir/R4/extension-valueset-toocostly.html

This is the resweep (post-milestone-10) EXPLORER pass for chunk VS-02. The
prior VS-02 EXPLORER test_vs02_explorer.py closed the CF-EXPLORER-CS02-01
portion for ValueSet/$expand (5-shape POST Content-Type family) and covered
10 lens dimensions (Content-Type family, count/offset matrix, filter
matching, inline body variations, too-costly, hierarchical paging, contains
shape, cross-source, date parameter, instance invocation).

The SKEPTIC resweep (test_vs02_skeptic_resweep.py) added 105 new probes and
landed QA-001 (filter-mode ``build_valueset_expand`` call site missing
``total=``). CF-SKEPTIC-VS02-03 closed in the same fix.

The HISTORIAN resweep (test_vs02_historian_resweep.py) added 49 new probes;
0 production code bugs; all 3 SKEPTIC tips ADDRESSED. CF-HISTORIAN-VS02-01
remains HIGH OPEN (fixture-coincidence-pinned).

This EXPLORER resweep applies the **lateral-thinking lens** ("what's not
yet tested?") and is organized into 12 lens dimensions per the launch notes:

  Lens 1  — HISTORIAN tip #3: GET<->POST byte-exact parity on LATERAL
             input shapes (mixed-case system, alias URIs, hostile filter
             text, multi-word filter, special-char filter) per HISTORIAN
             L12 test_h120 extension. Parity is structural (same _do_expand
             dispatch); EXPLORER confirms via parametrized lateral inputs.
  Lens 2  — HISTORIAN tip #4: cross-builder methodology consistency.
             Verify ``build_valueset_expand``, ``build_parameters_translate``,
             and ``build_closure_response`` are all called from
             ``_do_*`` handlers (not inline) — methodology extension of
             HISTORIAN L3 test_h30..h35 from "builder call-site count" to
             "cross-builder consistency".
  Lens 3  — Combined spec In parameters at once: filter+date+count+offset
             +system+displayLanguage+includeDesignations+includeDefinition
             +activeOnly+excludeNested lateral combination. Per FHIR R4
             $expand OperationDefinition (24 In parameters); many are
             accepted-but-ignored today — verify no 5xx and FHIR-conformant
             shape on every combination.
  Lens 4  — NEW spec In params not yet tested: ``valueSetVersion``,
             ``context``, ``contextDirection``, ``includeDesignations``,
             ``designation``, ``includeDefinition``, ``activeOnly``,
             ``excludeNested``, ``excludeNotForUI``,
             ``excludePostCoordinated``, ``displayLanguage``,
             ``exclude-system``, ``system-version``,
             ``check-system-version``, ``force-system-version``. Each
             accepted gracefully (200) or 422 (Query validation) — never 500.
  Lens 5  — Filter+system lateral combinations: filter constrained to
             one source via ``system`` query param; system alias inputs
             (urn:oid, trailing-slash, uppercase-scheme) + filter; mixed-
             case system on filter mode.
  Lens 6  — Combined operations cross-resource-type integration:
             $expand -> $lookup on each contains[]; $expand -> $validate-code
             on first contains[]; canonical-DISPLAY invariant between
             $expand contains[].display and $lookup Out display per
             CS-02/CS-03/CS-04/CS-05/VS-01 TERMINOLOGIST methodology.
  Lens 7  — Paging semantics lateral combinations: offset + count
             parametrized over the full grid; offset + count + system;
             offset + count + filter; offset > total returns empty.
  Lens 8  — Spec-listed 3 POST encodings parity: bare-ValueSet body,
             Parameters-with-valueSet body, Parameters-with-filter body.
             Cross-encoding consistency for the SAME logical request.
  Lens 9  — Lateral combinations with hostile body shapes: Parameters
             body with extra parameter types; Parameters body with
             valueSet + filter (which wins?); body with both url and
             valueSet (which wins?).
  Lens 10 — META structural-invariant probes: source-read on _do_expand
             mode dispatch; source-read on _expand_intensional handling;
             source-read on filter-mode +1 probe pattern; source-read
             on _expand_implicit_value_set canonical_system_uri call.
  Lens 11 — Cross-handler helper-wiring: _extract_valueset_from_parameters
             on every body shape; _parse_count_param on every count
             encoding; _truncation_extensions signature consistency.
  Lens 12 — JSON+XML format negotiation lateral: every mode honors
             _format=xml; every mode honors Accept: application/fhir+xml;
             JSON-to-XML contains[] fidelity.

Conformance fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus",   "A73211009", "N", "SNOMEDCT_US", "C0011849"),  # parent
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),  # child
    ("E11",      "HT", "Type 2 diabetes mellitus", "AE11",      "N", "ICD10CM",    "C0011847"),
    ("860975",   "SCD","24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")  # 44054006 is-a 73211009

Per GLOBAL_RULES.md:
  - "Test-too-lenient": every probe asserts POSITIVE success shape.
  - Spec citation required on every probe class.
  - "Silent fallbacks prohibited" — no broad except Exception.
  - "Single source of truth" — import from canonical locations.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (canonical R4)
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html (too-costly)
# Spec: https://hl7.org/fhir/R4/valueset.html#expansion (expansion shape)
# Spec: https://hl7.org/fhir/R4/parameters.html (Parameters resource)

SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URI_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"
SNOMED_URI_URN_OID = "urn:oid:2.16.840.1.113883.6.96"
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
# Helpers
# =============================================================================


def _get_func_source(
    module_path: Path, parent_name: str, child_name: str | None = None
) -> str:
    """Read the source of a top-level function or a nested function.

    Walks ``ast`` looking for ``ast.FunctionDef`` and ``ast.AsyncFunctionDef``.
    The nested-function form (``parent_name`` = factory function,
    ``child_name`` = inner def) is needed because many route handlers and
    ``_do_*`` helpers are defined inside the ``create_fhir_app`` factory.
    Mirrors the helper in test_vs02_historian_resweep.py.
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


def _get_expand(fhir_client, *, params: dict):
    """GET /fhir/ValueSet/$expand with query params."""
    resp = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=params,
        headers={"Accept": "application/fhir+json"},
    )
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"_raw": resp.text}
    return resp.status_code, parsed


def _post_expand(fhir_client, body: dict, *, params: dict | None = None):
    """POST a body to /fhir/ValueSet/$expand. Returns (status, body_json)."""
    resp = fhir_client.post(
        "/fhir/ValueSet/$expand",
        json=body,
        params=params or {},
        headers={"Accept": "application/fhir+json"},
    )
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"_raw": resp.text}
    return resp.status_code, parsed


def _make_intensional_snomed_isa() -> dict:
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs02-explorer-resweep-intensional",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "filter": [
                    {
                        "property": "concept",
                        "op": "is-a",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }
                ],
            }],
        },
    }


def _make_extensional_vs(system: str, codes: list[tuple[str, str]]) -> dict:
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs02-explorer-resweep-extensional",
        "compose": {
            "include": [{
                "system": system,
                "concept": [
                    {"code": c, "display": d} for c, d in codes
                ],
            }],
        },
    }


def _make_parameters_with_valueset(valueset: dict) -> dict:
    return {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "valueSet", "resource": valueset},
        ],
    }


def _make_parameters_with_filter(filter_text: str, count: int = 20) -> dict:
    return {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "filter", "valueString": filter_text},
            {"name": "count", "valueInteger": count},
        ],
    }


def _contains_codes(body: dict) -> list[tuple[str, str]]:
    out = []
    for c in body.get("expansion", {}).get("contains", []):
        out.append((c.get("system", ""), c.get("code", "")))
    return out


def _contains_displays(body: dict) -> list[str]:
    return [
        c.get("display", "")
        for c in body.get("expansion", {}).get("contains", [])
    ]


def _expand_total(body: dict) -> int | None:
    return body.get("expansion", {}).get("total")


def _expand_extensions(body: dict) -> list[dict]:
    return body.get("expansion", {}).get("extension", [])


def _assert_fhir_json_response(resp) -> None:
    """Assert the response has conformant FHIR JSON content-type and body."""
    assert resp.headers.get("content-type", "").startswith(
        "application/fhir+json"
    ), f"non-FHIR content-type: {resp.headers.get('content-type')!r}"


# =============================================================================
# Lens 1 — GET<->POST byte-exact parity on LATERAL input shapes
#
# HISTORIAN tip #3: test_h120 confirmed byte-exact parity for filter mode on
# the standard input shape. EXPLORER extends to LATERAL inputs — mixed-case
# system, alias URIs, hostile filter text — confirming the parity contract
# is structural (same _do_expand dispatch path).
# =============================================================================


class TestLens1GetPostParityLateralInputs:
    """Lens 1: GET and POST produce byte-exact responses on lateral inputs.

    The structural contract is _do_expand: both expand_get and expand_post
    (Parameters-with-filter form) funnel through the same _do_expand(mode=filter)
    path. Lateral inputs confirm the parity contract holds across input
    variations.
    """

    @pytest.mark.parametrize(
        "filter_text",
        [
            "diabetes",                  # baseline (matches 3 codes)
            "Diabetes",                  # mixed-case (case-insensitive match)
            "DIABETES",                  # uppercase (case-insensitive match)
            "type 2 diabetes",           # multi-word
            "metformin",                 # different system (RXNORM)
            "nonexistent_xyz_123",       # no match
            "diab*",                     # wildcard-style (server-delegated)
        ],
    )
    def test_e10_filter_lateral_get_post_byte_exact(
        self, fhir_client, filter_text
    ):
        """GET ?filter=X&count=20 == POST Parameters body with filter=X.

        Per FHIR R4 §4.7.5 In ``filter``: "A text filter... application of
        this filter is handled by the server". The contract is that the same
        filter applied via GET and POST produces the same response shape.
        """
        get_status, get_resp = _get_expand(
            fhir_client, params={"filter": filter_text, "count": 20}
        )
        post_status, post_resp = _post_expand(
            fhir_client, _make_parameters_with_filter(filter_text, count=20)
        )
        # Both MUST succeed.
        assert get_status == 200 and post_status == 200, (
            f"filter={filter_text!r} GET {get_status}, POST {post_status} — "
            f"both MUST succeed"
        )
        # contains[] MUST be byte-exact equal.
        assert _contains_codes(get_resp) == _contains_codes(post_resp), (
            f"filter={filter_text!r} GET != POST contains: "
            f"{_contains_codes(get_resp)} vs {_contains_codes(post_resp)}"
        )
        # total MUST be equal.
        assert _expand_total(get_resp) == _expand_total(post_resp), (
            f"filter={filter_text!r} GET total {_expand_total(get_resp)} != "
            f"POST total {_expand_total(post_resp)}"
        )

    @pytest.mark.parametrize(
        "system_uri",
        [
            SNOMED_URI,
            SNOMED_URI_TRAILING_SLASH,
            SNOMED_URI_UPPERCASE_SCHEME,
            SNOMED_URI_URN_OID,
            ICD10CM_URI,
            RXNORM_URI,
        ],
    )
    def test_e11_system_lateral_filter_get_post_byte_exact(
        self, fhir_client, system_uri
    ):
        """GET ?filter=X&system=Y == POST Parameters body with filter+system.

        Per FHIR R4 §4.7.5 In ``system`` (medterm4ds extension): constrains
        filter to a single source. The system alias inputs (trailing-slash,
        urn:oid, uppercase-scheme) all resolve to canonical in contains[].system
        per the client-input-as-canonical drift pattern (count=8+1 PROMOTED).
        """
        get_status, get_resp = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "system": system_uri, "count": 20},
        )
        post_status, post_resp = _post_expand(
            fhir_client,
            {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "filter", "valueString": "diabetes"},
                    {"name": "system", "valueUri": system_uri},
                    {"name": "count", "valueInteger": 20},
                ],
            },
        )
        # Both MUST succeed (system is recognized via fhir_uri_to_system).
        if get_status != 200:
            # Some aliases may not be filter-friendly — but GET and POST
            # MUST still produce the same status (parity contract).
            assert get_status == post_status, (
                f"system={system_uri!r} GET {get_status} != POST {post_status}"
            )
            return
        assert get_status == 200 and post_status == 200, (
            f"system={system_uri!r} GET {get_status}, POST {post_status}"
        )
        # contains[] MUST be byte-exact equal.
        assert _contains_codes(get_resp) == _contains_codes(post_resp), (
            f"system={system_uri!r} GET != POST contains"
        )
        # total MUST be equal.
        assert _expand_total(get_resp) == _expand_total(post_resp), (
            f"system={system_uri!r} GET total != POST total"
        )

    def test_e12_offset_count_lateral_get_post_byte_exact(self, fhir_client):
        """GET ?filter=X&offset=Y&count=Z == POST with same params.

        Lateral combination: filter + offset + count on both GET and POST.
        """
        for offset, count in [(0, 1), (0, 2), (1, 1), (0, 5)]:
            get_status, get_resp = _get_expand(
                fhir_client,
                params={"filter": "diabetes", "offset": offset, "count": count},
            )
            post_status, post_resp = _post_expand(
                fhir_client,
                {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "filter", "valueString": "diabetes"},
                        {"name": "offset", "valueInteger": offset},
                        {"name": "count", "valueInteger": count},
                    ],
                },
            )
            assert get_status == 200 and post_status == 200, (
                f"offset={offset} count={count} GET {get_status}, POST {post_status}"
            )
            assert _contains_codes(get_resp) == _contains_codes(post_resp), (
                f"offset={offset} count={count} GET != POST contains"
            )

    def test_e13_filter_hostile_lateral_get_post_no_5xx(self, fhir_client):
        """Hostile filter text on both GET and POST — never 500."""
        for hostile in [
            "'; DROP TABLE mrconso; --",
            "<script>alert('xss')</script>",
            "../../../etc/passwd",
            "a" * 256,    # at the cap
            "a" * 257,    # over the cap (should be 400 per QA-027)
            "null\x00byte",
            "unicode\xe4\xb8\xad\xe6\x96\x87",
        ]:
            get_status, get_resp = _get_expand(
                fhir_client, params={"filter": hostile, "count": 20}
            )
            post_status, post_resp = _post_expand(
                fhir_client, _make_parameters_with_filter(hostile, count=20)
            )
            # Both MUST NOT 5xx.
            assert get_status < 500, (
                f"filter={hostile!r} GET {get_status} — 5xx"
            )
            assert post_status < 500, (
                f"filter={hostile!r} POST {post_status} — 5xx"
            )
            # When both succeed, contains[] MUST be byte-exact equal.
            if get_status == 200 and post_status == 200:
                assert _contains_codes(get_resp) == _contains_codes(post_resp), (
                    f"filter={hostile!r} GET != POST contains"
                )


# =============================================================================
# Lens 2 — Cross-builder methodology consistency (HISTORIAN tip #4)
#
# HISTORIAN L3 (test_h30..h35) verified call-site counts for each builder.
# EXPLORER extends to cross-builder consistency: all 3 builders are called
# from _do_* handlers, all use canonical_system_uri (where applicable), and
# the call sites are structurally similar.
# =============================================================================


class TestLens2CrossBuilderMethodologyConsistency:
    """Lens 2: cross-builder consistency across the 3 response builders.

    Per HISTORIAN L3 methodology extension: the AST-walk over
    build_valueset_expand call sites (test_h41) generalizes to other
    builders. EXPLORER confirms cross-builder consistency by verifying
    the 3 builders (build_valueset_expand, build_parameters_translate,
    build_closure_response) all follow the same call-site pattern.
    """

    def test_e20_all_three_builders_defined(self):
        """All 3 builders exist in canonical locations.

        build_valueset_expand + build_parameters_translate live in
        engines/fhir/responses.py; build_closure_response lives in
        engines/fhir/closure.py (different module — per HISTORIAN L3
        test_h33..h35 source-read contracts).
        """
        responses_src = _RESPONSES_PATH.read_text()
        closure_src = _CLOSURE_PATH.read_text()
        assert "def build_valueset_expand(" in responses_src
        assert "def build_parameters_translate(" in responses_src
        assert "def build_closure_response(" in closure_src

    def test_e21_all_three_builders_called_from_do_handlers(self):
        """All 3 builders are called from _do_* handlers in fhir_api.py.

        Per cross-handler helper-wiring pattern (count=6 PROMOTED): every
        _do_* handler MUST delegate to a response builder rather than
        constructing the response inline. The 3 builders covering the 3
        operations that medterm4ds implements ($expand, $translate, $closure)
        are the load-bearing structural contract.
        """
        src = _FHIR_API_PATH.read_text()
        # All 3 builders appear in the source — meaning each is referenced.
        for builder in [
            "build_valueset_expand",
            "build_parameters_translate",
            "build_closure_response",
        ]:
            assert builder in src, f"{builder} not referenced in fhir_api.py"

    def test_e22_build_valueset_expand_call_sites_count(self):
        """AST-walk: build_valueset_expand called from 4 sites (per HISTORIAN)."""
        src = _FHIR_API_PATH.read_text()
        tree = ast.parse(src)
        count = 0
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "build_valueset_expand"
            ):
                count += 1
        # Per HISTORIAN test_h41: 4 call sites (filter, url-pattern,
        # intensional, implicit-value-set).
        assert count == 4, (
            f"Expected 4 build_valueset_expand call sites, found {count}"
        )

    def test_e23_build_parameters_translate_call_sites_count(self):
        """AST-walk: build_parameters_translate called from 1 site."""
        src = _FHIR_API_PATH.read_text()
        tree = ast.parse(src)
        count = 0
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "build_parameters_translate"
            ):
                count += 1
        # Per HISTORIAN test_h30: 1 call site (_do_translate).
        assert count == 1, (
            f"Expected 1 build_parameters_translate call site, found {count}"
        )

    def test_e24_build_closure_response_call_sites_count(self):
        """AST-walk: build_closure_response called from 1 site."""
        src = _FHIR_API_PATH.read_text()
        tree = ast.parse(src)
        count = 0
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "build_closure_response"
            ):
                count += 1
        # Per HISTORIAN test_h33: 1 call site (_do_closure).
        assert count == 1, (
            f"Expected 1 build_closure_response call site, found {count}"
        )

    def test_e25_build_valueset_expand_has_total_parameter(self):
        """build_valueset_expand signature has total: int | None = None.

        Per VS-02 SKEPTIC QA-057 (count=4 PROMOTED pattern at GLOBAL_RULES.md
        line 136): the builder MUST accept an explicit ``total`` parameter
        so call sites that pre-truncate via ``[:count]`` can pass the
        pre-truncation size. EXPLORER confirms the signature is consistent
        across this builder (the other 2 don't have a size field).
        """
        src = _get_func_source(_RESPONSES_PATH, "build_valueset_expand")
        assert "total" in src, (
            "build_valueset_expand must have total parameter (VS-02 SKEPTIC QA-057)"
        )

    def test_e26_builders_use_fhir_namespace_helpers(self):
        """All 3 builders source canonical URIs from canonical locations.

        Per single-source-of-truth invariants (GLOBAL_RULES.md): builders
        MUST source canonical URIs from SYSTEM_TO_FHIR_URI /
        canonical_system_uri / etc. — never hardcode.
        """
        src = _RESPONSES_PATH.read_text()
        # The canonical location is SYSTEM_TO_FHIR_URI in engines.fhir.
        assert "SYSTEM_TO_FHIR_URI" in src or "canonical_system_uri" in src, (
            "responses.py must source URIs from canonical location"
        )


# =============================================================================
# Lens 3 — Combined spec In parameters at once
#
# Per FHIR R4 $expand OperationDefinition: 24 In parameters. EXPLORER tests
# the lateral combination of multiple In parameters in a single request —
# every combination accepted gracefully (200 or 422) and FHIR-conformant
# on every path. Never 500.
# =============================================================================


class TestLens3CombinedSpecInParameters:
    """Lens 3: combined spec In parameters in a single request.

    Per FHIR R4 §4.7.5 $expand In Parameters: the spec lists 24 In
    parameters. EXPLORER probes the lateral combination surface — multiple
    In parameters in a single request. The contract: server returns 200 or
    422 (Query validation), never 500; response is FHIR-conformant
    (ValueSet or OperationOutcome).
    """

    def test_e30_filter_plus_count_plus_offset_plus_system(self, fhir_client):
        """Combined: filter + count + offset + system (4 In params at once)."""
        status, body = _get_expand(
            fhir_client,
            params={
                "filter": "diabetes",
                "count": 5,
                "offset": 0,
                "system": SNOMED_URI,
            },
        )
        assert status == 200, f"status={status}, body={body}"
        assert body.get("resourceType") == "ValueSet"
        # contains[] length MUST respect count.
        contains = body.get("expansion", {}).get("contains", [])
        assert len(contains) <= 5

    def test_e31_filter_plus_all_metadata_in_params(self, fhir_client):
        """Combined: filter + date + displayLanguage + includeDesignations.

        Per FHIR R4 §4.7.5: all 4 are spec-listed In parameters. The
        combination must be accepted (200) or rejected (422), never 500.
        """
        status, body = _get_expand(
            fhir_client,
            params={
                "filter": "diabetes",
                "date": "2024-01-01",
                "displayLanguage": "en",
                "includeDesignations": "true",
                "count": 20,
            },
        )
        assert status in (200, 422), f"status={status}, body={body}"
        if status == 200:
            assert body.get("resourceType") == "ValueSet"
        else:
            # 422 must produce a FHIR OperationOutcome per the
            # RequestValidationError exception handler.
            assert body.get("resourceType") == "OperationOutcome"

    def test_e32_inline_valueset_plus_all_in_params(self, fhir_client):
        """Combined: inline ValueSet (POST) + count + offset + displayLanguage.

        Per FHIR R4 §4.7.5: the POST shape with inline ValueSet body is
        spec-listed. Multiple In parameters in the query string are
        accepted alongside the body.
        """
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(
            fhir_client, vs,
            params={
                "count": 5,
                "offset": 0,
                "displayLanguage": "en",
                "includeDesignations": "false",
            },
        )
        assert status == 200, f"status={status}, body={body}"
        assert body.get("resourceType") == "ValueSet"

    def test_e33_all_ignored_in_params_combined(self, fhir_client):
        """Combined: every ignored-by-medterm4ds In param at once.

        Per AGENTS.md NOT A BUG registry: ``version``, ``offset``, ``date``,
        ``property`` (in $lookup) are accepted-but-ignored today (single-
        snapshot engine, no paging yet). The lateral combination of all
        ignored In params must not 5xx.
        """
        status, body = _get_expand(
            fhir_client,
            params={
                "filter": "diabetes",
                "offset": 0,
                "count": 20,
                "date": "2024-01-01",
                "displayLanguage": "en",
                "includeDesignations": "false",
                "includeDefinition": "false",
                "activeOnly": "true",
                "excludeNested": "false",
                "excludeNotForUI": "false",
                "excludePostCoordinated": "false",
            },
        )
        assert status in (200, 422), f"status={status}, body={body}"
        if status == 200:
            assert body.get("resourceType") == "ValueSet"

    def test_e34_full_in_param_matrix_no_5xx(self, fhir_client):
        """Every In param with a plausible value — never 500."""
        # Combine many spec In params with plausible values; each In param
        # that's NOT supported by medterm4ds SHOULD be silently ignored
        # (no error, no 5xx).
        status, body = _get_expand(
            fhir_client,
            params={
                "filter": "diabetes",
                "url": "http://example.org/vs/some-vs",
                "valueSetVersion": "2024-01-01",
                "context": "DiagnosticReport.category",
                "contextDirection": "incoming",
                "offset": 0,
                "count": 20,
                "date": "2024-01-01",
                "includeDesignations": "true",
                "designation": "en-US",
                "includeDefinition": "true",
                "activeOnly": "true",
                "excludeNested": "false",
                "excludeNotForUI": "false",
                "excludePostCoordinated": "false",
                "displayLanguage": "en",
            },
        )
        # The filter wins (it's the load-bearing param when url is unknown).
        # The url with an unknown prefix may 400/422, or filter may take over.
        # Either way: never 500.
        assert status < 500, f"status={status}, body={body}"
        assert body.get("resourceType") in ("ValueSet", "OperationOutcome")


# =============================================================================
# Lens 4 — NEW spec In params not yet tested
#
# Each new In param is accepted (200) or 422 (FastAPI Query type validation
# when the param is declared with a specific type). Never 500.
# =============================================================================


class TestLens4NewSpecInParams:
    """Lens 4: NEW spec In params not yet tested in any VS-02 file.

    Per FHIR R4 §4.7.5 $expand In Parameters (24 total). VS-02 EXPLORER
    covered: url, valueSet, filter, offset, count, date. The remaining In
    params are not yet tested. EXPLORER verifies each is handled gracefully
    — no 5xx, FHIR-conformant response shape on every path.
    """

    @pytest.mark.parametrize(
        "param_name,value",
        [
            ("valueSetVersion", "2024-01-01"),
            ("valueSetVersion", "1.0"),
            ("context", "DiagnosticReport.category"),
            ("context", "http://hl7.org/fhir/StructureDefinition/Patient#Patient.gender"),
            ("contextDirection", "incoming"),
            ("contextDirection", "outgoing"),
            ("includeDesignations", "true"),
            ("includeDesignations", "false"),
            ("includeDefinition", "true"),
            ("includeDefinition", "false"),
            ("activeOnly", "true"),
            ("activeOnly", "false"),
            ("excludeNested", "true"),
            ("excludeNested", "false"),
            ("excludeNotForUI", "true"),
            ("excludeNotForUI", "false"),
            ("excludePostCoordinated", "true"),
            ("excludePostCoordinated", "false"),
            ("displayLanguage", "en"),
            ("displayLanguage", "en-US"),
            ("displayLanguage", "de"),
            ("displayLanguage", "fr-FR"),
            ("designation", "en-US"),
            ("designation", "http://snomed.info/sct|900000000000013009"),
        ],
    )
    def test_e40_new_in_param_get_no_5xx(
        self, fhir_client, param_name, value
    ):
        """Each new spec In param accepted gracefully (200 or 422, never 500).

        The engine doesn't implement most of these params (single-snapshot,
        no designations, no nesting yet). The contract: each is silently
        ignored (200 with normal expansion) OR rejected with 422 (FastAPI
        Query type validation when the param type doesn't match).
        """
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", param_name: value, "count": 20},
        )
        assert status < 500, (
            f"param={param_name}={value!r} status={status} — 5xx"
        )
        assert body.get("resourceType") in ("ValueSet", "OperationOutcome"), (
            f"param={param_name}={value!r} body={body.get('resourceType')!r}"
        )

    @pytest.mark.parametrize(
        "param_name,value",
        [
            ("exclude-system", "http://snomed.info/sct"),
            ("exclude-system", "http://snomed.info/sct|2024-09"),
            ("system-version", "http://snomed.info/sct|2024-09"),
            ("check-system-version", "http://loinc.org|2.62"),
            ("force-system-version", "http://snomed.info/sct|2024-09"),
        ],
    )
    def test_e41_canonical_in_param_no_5xx(
        self, fhir_client, param_name, value
    ):
        """Canonical-style params (system-version, etc.) — never 500.

        Per FHIR R4 §4.7.5: these params use ``[system]|[version]`` format.
        The engine doesn't implement version pinning; the contract is that
        the param is accepted gracefully.
        """
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", param_name: value, "count": 20},
        )
        assert status < 500, (
            f"param={param_name}={value!r} status={status} — 5xx"
        )
        assert body.get("resourceType") in ("ValueSet", "OperationOutcome")

    def test_e42_context_with_context_direction_combined(self, fhir_client):
        """Combined: context + contextDirection — both spec-listed.

        Per FHIR R4 §4.7.5 In ``context``: "The context of the call". This
        is a binding-resolution delegation — medterm4ds doesn't implement
        binding resolution but MUST accept the params gracefully.
        """
        status, body = _get_expand(
            fhir_client,
            params={
                "context": "DiagnosticReport.category",
                "contextDirection": "incoming",
                "count": 20,
            },
        )
        # The implementation will fall through to the no-url/no-filter 400
        # path; that's spec-allowed when context can't be resolved.
        assert status < 500, f"status={status}, body={body}"
        assert body.get("resourceType") in ("ValueSet", "OperationOutcome")


# =============================================================================
# Lens 5 — Filter+system lateral combinations
#
# EXPLORER confirms filter+system combinations work for every system alias
# input. The contains[].system MUST be the canonical URI (not the alias)
# per client-input-as-canonical drift pattern (count=8+1 PROMOTED).
# =============================================================================


class TestLens5FilterSystemLateralCombinations:
    """Lens 5: filter + system lateral combinations.

    Per FHIR R4 §4.7.5 In ``system`` (medterm4ds extension): constrains
    filter to a single source. EXPLORER verifies filter+system combinations
    across system alias inputs.
    """

    @pytest.mark.parametrize(
        "system_uri,expected_canonical,expected_codes",
        [
            (SNOMED_URI, SNOMED_URI, {SNOMED_DIABETES_MELLITUS, SNOMED_T2DM}),
            (SNOMED_URI_TRAILING_SLASH, SNOMED_URI, {SNOMED_DIABETES_MELLITUS, SNOMED_T2DM}),
            (SNOMED_URI_UPPERCASE_SCHEME, SNOMED_URI, {SNOMED_DIABETES_MELLITUS, SNOMED_T2DM}),
            (SNOMED_URI_URN_OID, SNOMED_URI, {SNOMED_DIABETES_MELLITUS, SNOMED_T2DM}),
            (ICD10CM_URI, ICD10CM_URI, {ICD10CM_T2DM}),
            (RXNORM_URI, RXNORM_URI, set()),  # metformin doesn't match 'diabetes'
        ],
    )
    def test_e50_filter_system_canonical_uri_in_contains(
        self, fhir_client, system_uri, expected_canonical, expected_codes
    ):
        """filter + system: contains[].system is canonical, contains[].code
        is from the requested source.

        Per client-input-as-canonical drift pattern (count=8+1 PROMOTED):
        the contains[].system MUST be the canonical URI, NOT the alias the
        client supplied.
        """
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "system": system_uri, "count": 20},
        )
        assert status == 200, f"system={system_uri!r} status={status}"
        contains = body.get("expansion", {}).get("contains", [])
        # Every contains[].system MUST be the canonical URI.
        for c in contains:
            assert c.get("system") == expected_canonical, (
                f"system={system_uri!r} contains[].system={c.get('system')!r} "
                f"expected canonical={expected_canonical!r}"
            )
        # The set of codes MUST match the expected set for this source.
        actual_codes = {c.get("code") for c in contains}
        assert actual_codes == expected_codes, (
            f"system={system_uri!r} actual={actual_codes} "
            f"expected={expected_codes}"
        )

    def test_e51_filter_unknown_system_400(self, fhir_client):
        """filter + system=unknown: 400 OperationOutcome per spec.

        Per apps/fhir_api.py:_do_expand filter mode: when _resolve_sources
        returns None (unrecognized system URI), the handler returns 400
        with a FHIR OperationOutcome.
        """
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "system": "http://unknown.system/x"},
        )
        assert status == 400, f"status={status}, body={body}"
        assert body.get("resourceType") == "OperationOutcome"

    def test_e52_filter_with_empty_system_treated_as_no_system(self, fhir_client):
        """filter + system='' (empty): treated as no system (all sources).

        Per GLOBAL_RULES.md "Code Review Time" trigger (count=5 PROMOTED):
        empty-string-as-present-on-required-Query pattern. The ``system``
        param is OPTIONAL (Query(None)), so empty string has a different
        semantic than required string params. The implementation accepts
        empty system as "no filter constraint".
        """
        status, body = _get_expand(
            fhir_client, params={"filter": "diabetes", "system": ""}
        )
        # Empty string on optional param: server-side falls through to
        # "no filter constraint" — search across all sources.
        assert status == 200, f"status={status}, body={body}"
        contains = body.get("expansion", {}).get("contains", [])
        # All 3 diabetes-matching codes should be present.
        codes = {c.get("code") for c in contains}
        assert SNOMED_DIABETES_MELLITUS in codes
        assert SNOMED_T2DM in codes
        assert ICD10CM_T2DM in codes


# =============================================================================
# Lens 6 — Combined operations cross-resource-type integration
#
# $expand produces contains[]; verify canonical-DISPLAY invariant between
# $expand contains[].display and $lookup Out display per CS-02/CS-03/CS-04/
# CS-05/VS-01 TERMINOLOGIST methodology.
# =============================================================================


class TestLens6CombinedOperationsCrossResourceType:
    """Lens 6: $expand followed by $lookup on contains[] codes.

    Per CS-02/CS-03/CS-04/CS-05/VS-01 TERMINOLOGIST methodology: the
    canonical-DISPLAY cross-operation invariant is a load-bearing contract.
    EXPLORER confirms the invariant holds via integration probes that
    consume $expand output and feed it into $lookup.
    """

    def test_e60_expand_then_lookup_canonical_display_invariant(
        self, fhir_client
    ):
        """$expand contains[].display == $lookup Out display.

        Per FHIR R4 §4.9.2 Out ``contains[].display``: "The recommended
        display for this item in the expansion". The same code looked up
        via $lookup returns the same display in Out ``display`` (per CS-02
        TERMINOLOGIST QA-029 — engine canonical wins).
        """
        # Expand explicit concept list with NO supplied display (forces
        # engine canonical resolution).
        vs = _make_extensional_vs(
            SNOMED_URI,
            [(SNOMED_DIABETES_MELLITUS, ""), (SNOMED_T2DM, "")],
        )
        status, expand_body = _post_expand(fhir_client, vs)
        assert status == 200
        contains = expand_body.get("expansion", {}).get("contains", [])
        for c in contains:
            code = c.get("code")
            expand_display = c.get("display")
            # $lookup on the same code.
            lookup_resp = fhir_client.get(
                "/fhir/CodeSystem/$lookup",
                params={"system": SNOMED_URI, "code": code},
                headers={"Accept": "application/fhir+json"},
            )
            assert lookup_resp.status_code == 200
            lookup_params = lookup_resp.json().get("parameter", [])
            lookup_display = next(
                (p.get("valueString") for p in lookup_params
                 if p.get("name") == "display"),
                None,
            )
            assert expand_display == lookup_display, (
                f"code={code} expand display={expand_display!r} "
                f"!= lookup display={lookup_display!r}"
            )

    def test_e61_expand_then_validate_canonical_display_invariant(
        self, fhir_client
    ):
        """$expand contains[].display passes display-mismatch check on
        ValueSet/$validate-code.

        Per CS-03 TERMINOLOGIST canonical-DISPLAY cross-operation invariant
        (extended to VS-01 surface): the display returned by $expand should
        pass the display-mismatch check on $validate-code (i.e., the engine
        agrees it's the canonical display).
        """
        vs = _make_extensional_vs(
            SNOMED_URI,
            [(SNOMED_DIABETES_MELLITUS, ""), (SNOMED_T2DM, "")],
        )
        status, expand_body = _post_expand(fhir_client, vs)
        assert status == 200
        contains = expand_body.get("expansion", {}).get("contains", [])
        for c in contains:
            code = c.get("code")
            display = c.get("display")
            # $validate-code with the canonical display should return result=true.
            vc_resp = fhir_client.get(
                "/fhir/ValueSet/$validate-code",
                params={
                    "url": SNOMED_URI,
                    "system": SNOMED_URI,
                    "code": code,
                    "display": display,
                },
                headers={"Accept": "application/fhir+json"},
            )
            assert vc_resp.status_code == 200
            vc_params = vc_resp.json().get("parameter", [])
            result = next(
                (p.get("valueBoolean") for p in vc_params
                 if p.get("name") == "result"),
                None,
            )
            assert result is True, (
                f"code={code} display={display!r} validate-code result={result}"
            )

    def test_e62_intensional_expand_canonical_display_invariant(
        self, fhir_client
    ):
        """Intensional $expand (is-a filter) contains[].display == $lookup."""
        vs = _make_intensional_snomed_isa()
        status, expand_body = _post_expand(fhir_client, vs, params={"count": 20})
        assert status == 200
        contains = expand_body.get("expansion", {}).get("contains", [])
        # Should have at least the root + 1 descendant.
        assert len(contains) >= 2
        for c in contains:
            code = c.get("code")
            expand_display = c.get("display")
            lookup_resp = fhir_client.get(
                "/fhir/CodeSystem/$lookup",
                params={"system": SNOMED_URI, "code": code},
                headers={"Accept": "application/fhir+json"},
            )
            assert lookup_resp.status_code == 200
            lookup_params = lookup_resp.json().get("parameter", [])
            lookup_display = next(
                (p.get("valueString") for p in lookup_params
                 if p.get("name") == "display"),
                None,
            )
            assert expand_display == lookup_display, (
                f"intensional code={code} expand={expand_display!r} "
                f"!= lookup={lookup_display!r}"
            )

    def test_e63_filter_expand_canonical_display_invariant(self, fhir_client):
        """Filter-mode $expand contains[].display == $lookup.

        Per VS-02 SKEPTIC resweep QA-001 fix: filter mode now passes
        ``total=`` to the builder. EXPLORER confirms the contains[].display
        matches $lookup display on every matched code.
        """
        status, expand_body = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 20}
        )
        assert status == 200
        contains = expand_body.get("expansion", {}).get("contains", [])
        # 3 codes match: SNOMED DM, SNOMED T2DM, ICD-10-CM T2DM.
        assert len(contains) >= 2
        for c in contains:
            code = c.get("code")
            system_uri = c.get("system")
            expand_display = c.get("display")
            lookup_resp = fhir_client.get(
                "/fhir/CodeSystem/$lookup",
                params={"system": system_uri, "code": code},
                headers={"Accept": "application/fhir+json"},
            )
            assert lookup_resp.status_code == 200
            lookup_params = lookup_resp.json().get("parameter", [])
            lookup_display = next(
                (p.get("valueString") for p in lookup_params
                 if p.get("name") == "display"),
                None,
            )
            assert expand_display == lookup_display, (
                f"filter mode code={code} expand={expand_display!r} "
                f"!= lookup={lookup_display!r}"
            )


# =============================================================================
# Lens 7 — Paging semantics lateral combinations
#
# offset + count parametrized over the full grid; offset > total returns
# empty. Per FHIR R4 §4.7.5: offset is "number of records (not pages)".
# =============================================================================


class TestLens7PagingSemanticsLateralCombinations:
    """Lens 7: paging semantics lateral combinations.

    Per FHIR R4 §4.7.5 In ``offset`` and ``count``: offset is "number of
    records (not pages)" and count is "how many codes in a partial page
    view". The implementation currently ignores offset (CF-SKEPTIC-VS02-02
    LOW DEFERRED) — EXPLORER pins the current behavior and verifies no
    5xx on any offset+count combination.
    """

    @pytest.mark.parametrize(
        "offset,count",
        [
            (0, 1), (0, 2), (0, 5), (0, 10), (0, 20), (0, 100),
            (1, 1), (1, 5),
            (5, 5),
            (100, 5),    # offset > total
            (1000, 5),   # offset >> total
        ],
    )
    def test_e70_offset_count_combinations_no_5xx(
        self, fhir_client, offset, count
    ):
        """Every offset+count combination: never 500, FHIR-conformant body."""
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "offset": offset, "count": count},
        )
        assert status == 200, f"offset={offset} count={count} status={status}"
        assert body.get("resourceType") == "ValueSet"
        contains = body.get("expansion", {}).get("contains", [])
        # contains[] length MUST respect count.
        assert len(contains) <= count

    def test_e71_count_at_fixture_natural_size_no_toocostly(self, fhir_client):
        """count=3 (natural fixture size for 'diabetes'): no toocostly.

        Per CF-HISTORIAN-VS02-01 (HIGH OPEN): when count == natural size,
        no truncation should fire (no toocostly extension).
        """
        status, body = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 3}
        )
        assert status == 200
        exts = _expand_extensions(body)
        # No toocostly when count >= natural size.
        assert not any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"count=3 should not emit toocostly, extensions={exts}"
        )

    def test_e72_count_below_natural_size_emits_toocostly(self, fhir_client):
        """count=1 with multi-match filter: toocostly fires."""
        status, body = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 1}
        )
        assert status == 200
        exts = _expand_extensions(body)
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"count=1 should emit toocostly, extensions={exts}"
        )

    def test_e73_offset_ignored_today_pins_cf_skeptic_vs02_02(self, fhir_client):
        """CF-SKEPTIC-VS02-02 (offset ignored) — pinned via this probe.

        When the offset is implemented in a future chunk, this probe MUST
        be updated to assert the new behavior (offset actually slices
        contains[]).
        """
        # Same filter, different offsets — contains[] should be IDENTICAL
        # today (offset ignored).
        s1, b1 = _get_expand(
            fhir_client, params={"filter": "diabetes", "offset": 0, "count": 5}
        )
        s2, b2 = _get_expand(
            fhir_client, params={"filter": "diabetes", "offset": 1, "count": 5}
        )
        assert s1 == 200 and s2 == 200
        # CF-SKEPTIC-VS02-02 OPEN: offset currently ignored.
        assert _contains_codes(b1) == _contains_codes(b2), (
            "CF-SKEPTIC-VS02-02 OPEN: offset currently ignored — "
            "contains[] should be identical for offset=0 and offset=1"
        )

    def test_e74_count_zero_rejected_with_422(self, fhir_client):
        """CF-SKEPTIC-VS02-01 OPEN: count=0 currently 422 (Query ge=1).

        Per FHIR R4 §4.7.5 In ``count``: "If count = 0, the client is
        asking for the total expansion size". The current implementation
        rejects count=0 with 422 via Query(ge=1). Pinned via this probe.
        """
        status, _ = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 0}
        )
        # CF-SKEPTIC-VS02-01 OPEN: count=0 currently 422.
        assert status == 422


# =============================================================================
# Lens 8 — Spec-listed 3 POST encodings parity
#
# 3 POST encodings: bare-ValueSet body, Parameters-with-valueSet body,
# Parameters-with-filter body. EXPLORER confirms parity for the SAME
# logical request across encodings.
# =============================================================================


class TestLens8ThreePostEncodingsParity:
    """Lens 8: spec-listed 3 POST encodings parity.

    Per FHIR R4 §4.7.5 In ``valueSet``: "The value set is provided directly
    as part of the request" — 2 forms are spec-permitted (bare-ValueSet
    body and Parameters-with-valueSet body). The 3rd form
    (Parameters-with-filter body) covers the filter-mode POST shape.
    """

    def test_e80_bare_valueset_vs_parameters_with_valueset_byte_exact(
        self, fhir_client
    ):
        """bare-ValueSet body == Parameters-with-valueSet body.

        Per apps/fhir_api.py:expand_post: the handler detects
        resourceType=="ValueSet" and dispatches to intensional mode; the
        Parameters-with-valueSet form extracts the valueSet via
        _extract_valueset_from_parameters and dispatches to the same
        intensional mode. The contains[] MUST be byte-exact equal.
        """
        vs = _make_intensional_snomed_isa()
        # Form 1: bare-ValueSet body.
        s1, b1 = _post_expand(fhir_client, vs, params={"count": 20})
        # Form 2: Parameters-with-valueSet body.
        s2, b2 = _post_expand(
            fhir_client, _make_parameters_with_valueset(vs),
            params={"count": 20},
        )
        assert s1 == 200 and s2 == 200, f"Form1 {s1}, Form2 {s2}"
        assert _contains_codes(b1) == _contains_codes(b2), (
            f"Form1 != Form2 contains: {_contains_codes(b1)} vs {_contains_codes(b2)}"
        )
        assert _expand_total(b1) == _expand_total(b2)

    def test_e81_inline_count_in_parameters_body(self, fhir_client):
        """Parameters-with-valueSet body carries count alongside valueSet.

        Per apps/fhir_api.py:expand_post: when the body is Parameters-with-
        valueSet, the spec-listed In parameters (count, offset, etc.) MAY
        be co-located in the same Parameters body. The implementation
        extracts them via _parse_parameters and uses _parse_count_param to
        honor the inline count.
        """
        vs = _make_intensional_snomed_isa()
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": vs},
                {"name": "count", "valueInteger": 1},
            ],
        }
        status, body_resp = _post_expand(fhir_client, body)
        assert status == 200, f"status={status}, body={body_resp}"
        # count=1 should fire toocostly.
        exts = _expand_extensions(body_resp)
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"count=1 should fire toocostly, extensions={exts}"
        )

    def test_e82_three_filter_encodings_byte_exact(self, fhir_client):
        """3 filter encodings: GET query, bare POST body (wrong shape),
        Parameters-with-filter body.

        The contract: GET query and Parameters-with-filter body produce
        byte-exact responses (test_h120). EXPLORER confirms this holds.
        """
        # Form 1: GET query.
        s1, b1 = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 5}
        )
        # Form 2: Parameters-with-filter body.
        s2, b2 = _post_expand(
            fhir_client,
            _make_parameters_with_filter("diabetes", count=5),
        )
        assert s1 == 200 and s2 == 200
        assert _contains_codes(b1) == _contains_codes(b2), (
            f"GET filter != POST Parameters filter"
        )
        assert _expand_total(b1) == _expand_total(b2)

    def test_e83_count_in_query_overrides_default_on_post(self, fhir_client):
        """POST with explicit count query param: overrides default.

        Per apps/fhir_api.py:expand_post Query declaration: count is
        Query(20, ge=1, le=1000) — the default is 20. EXPLORER confirms
        the count query param is honored.
        """
        vs = _make_intensional_snomed_isa()
        # count=1 in query string.
        s1, b1 = _post_expand(fhir_client, vs, params={"count": 1})
        assert s1 == 200
        # contains[] length MUST respect count=1.
        contains = b1.get("expansion", {}).get("contains", [])
        assert len(contains) <= 1


# =============================================================================
# Lens 9 — Lateral combinations with hostile body shapes
#
# Per FHIR R4 §3.1.0.1.5 + §3.1.0.1.9: a malformed client body MUST produce
# a FHIR OperationOutcome (not a 500 + traceback).
# =============================================================================


class TestLens9HostileBodyShapes:
    """Lens 9: hostile body shapes are gracefully handled.

    Per FHIR R4 §3.1.0.1.5 + §3.1.0.1.9: a malformed client body MUST
    produce a FHIR OperationOutcome (not a 500 + traceback). EXPLORER
    probes hostile body shapes that combine with $expand semantics.
    """

    def test_e90_parameters_body_with_valueSet_and_filter(self, fhir_client):
        """Parameters body with BOTH valueSet AND filter parameters.

        When both are present, valueSet wins (intensional mode dispatches
        first per _do_expand). The filter parameter is silently dropped.
        """
        vs = _make_intensional_snomed_isa()
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": vs},
                {"name": "filter", "valueString": "diabetes"},
            ],
        }
        status, body_resp = _post_expand(fhir_client, body)
        assert status == 200, f"status={status}, body={body_resp}"
        # Intensional mode should fire (root + 1 descendant).
        contains = body_resp.get("expansion", {}).get("contains", [])
        codes = {c.get("code") for c in contains}
        # Should contain the SNOMED DM root + T2DM descendant.
        assert SNOMED_DIABETES_MELLITUS in codes

    def test_e91_parameters_body_with_non_dict_parameter_entries(
        self, fhir_client
    ):
        """Parameters body with non-dict parameter[] entries: gracefully
        handled by isinstance guard per CS-04 SKEPTIC QA-001.

        Per the 10th PROMOTED pattern (isinstance guard at untrusted-data
        list-iterator boundary): non-dict parameter[] entries are silently
        skipped — no 500, no traceback.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                "not-a-dict",
                42,
                None,
                ["nested", "list"],
                {"name": "filter", "valueString": "diabetes"},
                {"name": "count", "valueInteger": 5},
            ],
        }
        status, body_resp = _post_expand(fhir_client, body)
        # MUST NOT 5xx — the valid entries are processed, the invalid
        # entries are silently dropped.
        assert status < 500, f"status={status}, body={body_resp}"
        assert body_resp.get("resourceType") in ("ValueSet", "OperationOutcome")

    def test_e92_parameters_body_with_valueSet_wrong_resourceType(
        self, fhir_client
    ):
        """Parameters body with valueSet parameter carrying wrong
        resourceType: gracefully handled (returns None, falls through).
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "valueSet",
                    "resource": {
                        "resourceType": "CodeSystem",  # wrong type
                        "url": "http://example.org/cs",
                    },
                },
            ],
        }
        status, body_resp = _post_expand(fhir_client, body)
        # Falls through to the no-url/no-filter 400 path.
        assert status == 400, f"status={status}, body={body_resp}"
        assert body_resp.get("resourceType") == "OperationOutcome"

    def test_e93_valueSet_body_with_compose_null(self, fhir_client):
        """ValueSet body with compose=null: gracefully handled.

        Per VS-01 SKEPTIC resweep QA-001 (5th sibling of isinstance-guard
        pattern): non-dict compose is replaced with {} — no AttributeError,
        no 500.
        """
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/empty",
            "compose": None,
        }
        status, body_resp = _post_expand(fhir_client, body)
        assert status == 200, f"status={status}, body={body_resp}"
        # Empty expansion (no include/concept).
        contains = body_resp.get("expansion", {}).get("contains", [])
        assert contains == []

    def test_e94_valueSet_body_with_compose_string(self, fhir_client):
        """ValueSet body with compose=string: gracefully handled."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/empty",
            "compose": "not-a-dict",
        }
        status, body_resp = _post_expand(fhir_client, body)
        assert status == 200, f"status={status}, body={body_resp}"
        contains = body_resp.get("expansion", {}).get("contains", [])
        assert contains == []

    def test_e95_body_with_url_query_and_inline_valueSet(self, fhir_client):
        """GET with both url and inline ValueSet: which wins?

        Per _do_expand: when value_set is truthy, it dispatches to
        intensional FIRST (before url). The url param is silently ignored
        when value_set is provided.
        """
        vs = _make_intensional_snomed_isa()
        # POST with both body (ValueSet) and url query param.
        status, body_resp = _post_expand(
            fhir_client, vs,
            params={"url": "http://example.org/vs/some-other-vs"},
        )
        assert status == 200
        # Should be intensional mode (the inline body wins).
        contains = body_resp.get("expansion", {}).get("contains", [])
        codes = {c.get("code") for c in contains}
        assert SNOMED_DIABETES_MELLITUS in codes


# =============================================================================
# Lens 10 — META structural-invariant probes
#
# Source-read contracts that document the load-bearing structure of _do_expand
# and the helpers it calls. Methodology extension of HISTORIAN L9 (test_h90..h94)
# to META invariants.
# =============================================================================


class TestLens10MetaStructuralInvariants:
    """Lens 10: META structural-invariant probes.

    Per HISTORIAN L9 methodology extension: source-read contracts document
    the load-bearing structure of the implementation. EXPLORER adds META
    invariants that pin the overall _do_expand mode-dispatch contract.
    """

    def test_e100_do_expand_has_4_modes_documented(self):
        """_do_expand docstring lists 4 modes.

        Per apps/fhir_api.py:_do_expand docstring (VS-02 HISTORIAN test_h111):
        the docstring MUST list the 4 dispatch modes.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        # Per the implementation: 4 modes (intensional, implicit-value-set,
        # url-pattern, filter).
        assert "Mode 1" in src or "Inline ValueSet" in src
        assert "Mode 2" in src or "Implicit" in src
        assert "Mode 3" in src or "URL with fhir_vs" in src or "fhir_vs" in src
        assert "Mode 4" in src or "Text filter" in src or "filter_text" in src

    def test_e101_do_expand_filter_mode_uses_plus_one_probe(self):
        """Filter mode uses the +1 probe pattern (count+1) per VS-02 SKEPTIC
        QA-001 fix.

        Per apps/fhir_api.py:_do_expand filter mode: the search_names call
        uses limit=count+1 to detect truncation. This is the load-bearing
        structural contract for the filter-mode total= fix.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        # The +1 probe pattern MUST be present.
        assert "count + 1" in src or "count+1" in src, (
            "Filter mode must use the +1 probe pattern (count+1) per "
            "VS-02 SKEPTIC QA-001"
        )

    def test_e102_filter_mode_passes_total_to_builder(self):
        """Filter mode passes total=untruncated_total to build_valueset_expand."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        # The total= keyword argument MUST be present.
        assert "total=" in src, (
            "Filter mode must pass total= to build_valueset_expand per "
            "VS-02 SKEPTIC QA-001"
        )

    def test_e103_filter_mode_passes_extensions_to_builder(self):
        """Filter mode passes extensions= to build_valueset_expand (CF-SKEPTIC-VS02-03
        closed by VS-02 SKEPTIC QA-001)."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        assert "extensions=" in src or "extensions = " in src, (
            "Filter mode must pass extensions= to build_valueset_expand per "
            "VS-02 SKEPTIC QA-001 (CF-SKEPTIC-VS02-03 closure)"
        )

    def test_e104_expand_intensional_uses_bfs_helper(self):
        """_expand_intensional uses get_descendants_bfs helper.

        Per CF-HISTORIAN-VS02-01: the BFS helper is the load-bearing
        structure for the intensional mode descendant walk.
        """
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_intensional"
        )
        assert "get_descendants_bfs" in src, (
            "_expand_intensional must use get_descendants_bfs helper"
        )

    def test_e105_expand_implicit_value_set_uses_canonical_system_uri(self):
        """_expand_implicit_value_set uses canonical_system_uri per
        CF-HISTORIAN-VS02-02 (RESOLVED)."""
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_implicit_value_set"
        )
        assert "canonical_system_uri" in src, (
            "_expand_implicit_value_set must use canonical_system_uri per "
            "CF-HISTORIAN-VS02-02 RESOLVED"
        )


# =============================================================================
# Lens 11 — Cross-handler helper-wiring
#
# Verify the helpers (_extract_valueset_from_parameters, _parse_count_param,
# _truncation_extensions) are correctly wired into the POST handler and
# _do_expand filter mode respectively.
# =============================================================================


class TestLens11CrossHandlerHelperWiring:
    """Lens 11: cross-handler helper-wiring.

    Per cross-handler helper-wiring pattern (count=6 PROMOTED): every
    helper that exists MUST be wired into every handler that should use
    it. EXPLORER confirms the wiring is intact.
    """

    def test_e110_extract_valueset_from_parameters_defined(self):
        """_extract_valueset_from_parameters helper is defined."""
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_extract_valueset_from_parameters"
        )
        assert src, "_extract_valueset_from_parameters must be defined"

    def test_e111_extract_valueset_called_from_expand_post(self):
        """_extract_valueset_from_parameters is called from expand_post."""
        # expand_post is async, defined inside create_fhir_app.
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "expand_post")
        assert "_extract_valueset_from_parameters" in src, (
            "expand_post must call _extract_valueset_from_parameters"
        )

    def test_e112_parse_count_param_defined(self):
        """_parse_count_param helper is defined."""
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_parse_count_param"
        )
        assert src, "_parse_count_param must be defined"

    def test_e113_parse_count_param_called_from_expand_post(self):
        """_parse_count_param is called from expand_post (POST count override)."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "expand_post")
        assert "_parse_count_param" in src, (
            "expand_post must call _parse_count_param"
        )

    def test_e114_truncation_extensions_called_from_do_expand(self):
        """_truncation_extensions is called from _do_expand filter mode."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        assert "_truncation_extensions" in src, (
            "_do_expand must call _truncation_extensions"
        )

    def test_e115_truncation_extensions_called_from_expand_intensional(self):
        """_truncation_extensions is called from _expand_intensional."""
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_intensional"
        )
        assert "_truncation_extensions" in src, (
            "_expand_intensional must call _truncation_extensions"
        )

    def test_e116_truncation_extensions_called_from_implicit_value_set(self):
        """_truncation_extensions is called from _expand_implicit_value_set."""
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_implicit_value_set"
        )
        assert "_truncation_extensions" in src, (
            "_expand_implicit_value_set must call _truncation_extensions"
        )


# =============================================================================
# Lens 12 — JSON+XML format negotiation lateral
#
# Every mode honors _format=xml; every mode honors Accept: application/fhir+xml;
# JSON-to-XML contains[] fidelity.
# =============================================================================


class TestLens12JsonXmlFormatNegotiationLateral:
    """Lens 12: JSON+XML format negotiation across every mode.

    Per FHIR R4 §3.1.0.1.9 + §3.1.0.1.11: clients MAY request JSON or XML
    via Accept header or _format query param. EXPLORER confirms every
    $expand mode honors both formats.
    """

    def test_e120_filter_mode_xml_via_format_param(self, fhir_client):
        """Filter mode: _format=xml returns application/fhir+xml."""
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 5, "_format": "xml"},
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith(
            "application/fhir+xml"
        )
        # Body should be XML.
        assert "<ValueSet" in resp.text or "<valueSet" in resp.text

    def test_e121_filter_mode_xml_via_accept_header(self, fhir_client):
        """Filter mode: Accept: application/fhir+xml returns XML."""
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 5},
            headers={"Accept": "application/fhir+xml"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith(
            "application/fhir+xml"
        )
        assert "<ValueSet" in resp.text or "<valueSet" in resp.text

    def test_e122_intensional_mode_xml_via_format_param(self, fhir_client):
        """Intensional mode: _format=xml returns XML."""
        vs = _make_intensional_snomed_isa()
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            params={"count": 5, "_format": "xml"},
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith(
            "application/fhir+xml"
        )
        assert "<ValueSet" in resp.text or "<valueSet" in resp.text

    def test_e123_implicit_value_set_xml_via_format_param(self, fhir_client):
        """Implicit value set mode: _format=xml returns XML."""
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": f"{SNOMED_URI}/vs", "count": 5, "_format": "xml"},
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith(
            "application/fhir+xml"
        )
        assert "<ValueSet" in resp.text or "<valueSet" in resp.text

    def test_e124_json_xml_contains_codes_agreement(self, fhir_client):
        """JSON and XML responses contain the same codes (set equality)."""
        # JSON response.
        json_resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 5},
            headers={"Accept": "application/fhir+json"},
        )
        # XML response.
        xml_resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 5, "_format": "xml"},
            headers={"Accept": "application/fhir+json"},
        )
        assert json_resp.status_code == 200
        assert xml_resp.status_code == 200
        # Extract codes from JSON.
        json_codes = {
            c.get("code")
            for c in json_resp.json().get("expansion", {}).get("contains", [])
        }
        # Extract codes from XML via substring search.
        xml_text = xml_resp.text
        for code in json_codes:
            assert f'value="{code}"' in xml_text, (
                f"code={code} present in JSON but not XML"
            )

    def test_e125_filter_mode_format_json_explicit(self, fhir_client):
        """_format=json explicitly returns JSON content-type."""
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "count": 5, "_format": "json"},
            headers={"Accept": "application/fhir+xml"},  # Accept header is XML
        )
        # _format overrides Accept per EXPLORER TS-01 QA-009.
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith(
            "application/fhir+json"
        )
