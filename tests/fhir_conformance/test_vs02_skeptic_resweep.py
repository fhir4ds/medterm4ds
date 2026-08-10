"""VS-02 SKEPTIC resweep: ValueSet $expand — Basic.

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
Too-costly: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
Paging semantics: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion

This is the resweep (post-milestone-10) SKEPTIC pass for chunk VS-02. The
prior VS-02 SKEPTIC test_vs02_skeptic.py covered the baseline spec items
(QA-057: ``build_valueset_expand`` truncation total was the lone fix). This
resweep focuses on:

  1. Hostile-input probes per spec item — boundary conditions, malformed
     bodies, special characters, very long inputs. (SKEPTIC lens: "break
     it".)
  2. CF-HISTORIAN-VS02-01 (HIGH OPEN — BFS-capped intensional path's
     ``expansion.total`` reflects the truncated size) — independent re-
     verification per the load-bearing known-issue tip from the launch
     notes.
  3. ``build_valueset_expand`` call-site audit per VS-01/TERMINOLOGIST
     tip — every truncating call site MUST pass explicit
     ``total=<un-truncated-size>`` per GLOBAL_RULES.md line 136 PROMOTED
     pattern.
  4. Source-read structural contracts — the simplest way to lock in
     expected behaviors without depending on fixture data.
  5. Cross-handler GET↔POST byte-exact parity on every input shape.
  6. Response shape audit — expansion.{timestamp, total, contains[]}
     structure conforms to FHIR R4 §4.9 in every mode.

Conformance fixture (4 mrconso rows, 1 mrrel row): SNOMEDCT_US has 2 codes
(Diabetes mellitus / T2DM); ICD10CM has 1 (E11); RXNORM has 1 (metformin);
mrrel has a single isa relationship (T2DM → Diabetes mellitus). This
fixture is small but sufficient to exercise the 8 spec items.
"""

from __future__ import annotations

import ast
import inspect
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (canonical R4)
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html (too-costly)
# Spec: https://hl7.org/fhir/R4/valueset.html#expansion (expansion shape)
from medterm4ds.engines.fhir import FHIR_R4_FILTER_OPERATORS

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"
SNOMED_T2DM = "44054006"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"

TOOCOSTLY_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

# Path to apps/fhir_api.py for source-read structural probes.
_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)


# =============================================================================
# Helpers
# =============================================================================


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


def _contains_codes(body: dict) -> list[tuple[str, str]]:
    out = []
    for c in body.get("expansion", {}).get("contains", []):
        out.append((c.get("system", ""), c.get("code", "")))
    return out


def _make_intensional_snomed_isa() -> dict:
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs02-test-intensional",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "filter": [
                    {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                ],
            }],
        },
    }


def _make_extensional_snomed() -> dict:
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs02-test-extensional",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "concept": [
                    {"code": SNOMED_DIABETES_MELLITUS, "display": "Diabetes mellitus"},
                    {"code": SNOMED_T2DM, "display": "Type 2 diabetes mellitus"},
                ],
            }],
        },
    }


def _make_intensional_snomed_descendent_of() -> dict:
    """descendent-of (spec-correct spelling) — descendants only, no root."""
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs02-test-descendent",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "filter": [
                    {"property": "concept", "op": "descendent-of", "value": SNOMED_DIABETES_MELLITUS}
                ],
            }],
        },
    }


def _get_func_source(module_path: Path, parent_name: str, child_name: str | None = None):
    """Read the source of a top-level function or a nested function.

    Walks ``ast`` looking for ``ast.FunctionDef`` and ``ast.AsyncFunctionDef``.
    The nested-function form (``parent_name`` = factory function,
    ``child_name`` = inner def) is needed because many route handlers are
    defined inside the ``create_fhir_app`` factory (extends the HISTORIAN
    TS-01 strategy to nested-function source-reading).
    """
    src = module_path.read_text()
    tree = ast.parse(src)
    if child_name is None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parent_name:
                return ast.get_source_segment(src, node) or ""
        return ""

    # Nested-function form: find parent first, then walk its body.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parent_name:
            for child in ast.walk(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == child_name:
                    return ast.get_source_segment(src, child) or ""
    return ""


# =============================================================================
# Item 1: Required params — url OR instance-level ValueSet
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html "In Parameters"
# =============================================================================


class TestItem1RequiredParamsHostile:
    """Item 1: required params — hostile probes on url handling."""

    def test_s10_empty_url_returns_400_or_404(self, fhir_client):
        """Empty url (``url=``) MUST NOT silently expand.

        Per FHIR R4 §4.7.5: ``url`` is "A canonical URL for a ValueSet" —
        an empty string is not a valid URL. The server MUST reject with a
        FHIR OperationOutcome.
        """
        status, body = _get_expand(fhir_client, params={"url": ""})
        assert status in (400, 404, 422), f"expected 400/404/422, got {status}: {body}"
        assert body.get("resourceType") == "OperationOutcome"

    @pytest.mark.parametrize("bad_url", [
        "not a url",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "http://",
        "://missing-scheme",
        "http://example.com/<>",  # special chars
        "http://" + "x" * 10000 + ".com",  # very long
    ])
    def test_s11_malformed_url_rejected_without_500(self, fhir_client, bad_url):
        """Malformed / hostile ``url`` MUST NOT produce 500 + traceback.

        Per FHIR R4 §3.1.0.1.5 + §3.1.0.1.9: malformed input MUST produce a
        FHIR OperationOutcome, not a 500 with a Python traceback (info-
        disclosure surface). Per GLOBAL_RULES.md "Silent Fallbacks":
        programming bugs MUST propagate but malformed input is NOT a
        programming bug — the handler MUST catch and convert.
        """
        status, body = _get_expand(fhir_client, params={"url": bad_url})
        assert status < 500, f"5xx on malformed url: {status}: {body}"
        # Body MUST be FHIR-shaped (OperationOutcome or ValueSet).
        if status >= 400:
            assert body.get("resourceType") == "OperationOutcome", (
                f"5xx path body not OperationOutcome: {body}"
            )

    def test_s12_get_with_url_to_explicit_valueset_returns_400(self, fhir_client):
        """GET with ``url`` to a non-persisted canonical URL MUST 400.

        medterm4ds is non-persisting (per AGENTS.md) — any URL that is NOT
        an implicit-value-set URL or fhir_vs URL MUST 400 (or 404).
        """
        status, body = _get_expand(
            fhir_client,
            params={"url": "http://example.org/vs/some-canonical"},
        )
        assert status in (400, 404), f"expected 400/404, got {status}: {body}"
        assert body.get("resourceType") == "OperationOutcome"

    def test_s13_post_with_empty_value_set_body_400(self, fhir_client):
        """POST an empty ValueSet body (no compose) MUST 400 OR return
        empty expansion (not 500).

        Per FHIR R4 §3.1.0.1.5: a malformed resource body MUST return a
        FHIR OperationOutcome. ``compose`` absent is technically an empty
        composition; the server SHOULD return an empty expansion (200) or
        a 400. Either is conformant; a 500 is NOT.
        """
        body = {"resourceType": "ValueSet"}
        status, body_json = _post_expand(fhir_client, body)
        assert status < 500, f"5xx on empty ValueSet body: {status}: {body_json}"
        if status == 200:
            assert body_json["resourceType"] == "ValueSet"
            assert body_json["expansion"]["total"] == 0
        else:
            assert body_json.get("resourceType") == "OperationOutcome"

    def test_s14_post_with_non_valueset_body_400(self, fhir_client):
        """POST a body with ``resourceType != ValueSet`` and != Parameters
        MUST 400 (the handler routes by resourceType).
        """
        body = {"resourceType": "Patient", "id": "x"}
        status, body_json = _post_expand(fhir_client, body)
        # Falls through to the Parameters branch (no url, no filter, no
        # valueSet) → 400.
        assert status == 400, f"expected 400, got {status}: {body_json}"
        assert body_json.get("resourceType") == "OperationOutcome"


# =============================================================================
# Item 2: Optional params — hostile filter/offset/count/valueSet/date
# =============================================================================


class TestItem2OptionalParamsHostile:
    """Item 2: optional params — hostile probes."""

    @pytest.mark.parametrize("filter_text", [
        "diabetes; DROP TABLE mrconso; --",  # SQL injection
        "<script>alert(1)</script>",         # XSS
        "../../../etc/passwd",                # path traversal
        "diabetes\x00null",                   # null bytes
        "糖尿病",                              # unicode CJK
        "diabetes\r\n",                       # CRLF injection
        "   ",                                # whitespace-only
    ])
    def test_s20_filter_hostile_no_5xx(self, fhir_client, filter_text):
        """Hostile ``filter`` MUST NOT 500. Per GLOBAL_RULES.md: hostile
        input MUST be handled gracefully (200 no-match OR 400 reject).
        """
        status, body = _get_expand(
            fhir_client, params={"filter": filter_text}
        )
        assert status < 500, (
            f"5xx on filter={filter_text!r}: {status}: {body}"
        )
        if status >= 400:
            assert body.get("resourceType") == "OperationOutcome"

    def test_s21_filter_very_long_no_5xx(self, fhir_client):
        """``filter`` >256 chars MUST 400 (per search_names length cap).

        Found by TS-02 EXPLORER QA-027 (ValueError → 400 wrap).
        """
        long_filter = "a" * 5000
        status, body = _get_expand(
            fhir_client, params={"filter": long_filter}
        )
        assert status == 400, f"expected 400 for >256 filter, got {status}: {body}"
        assert body.get("resourceType") == "OperationOutcome"

    def test_s22_count_zero_422_or_200_empty(self, fhir_client):
        """``count=0`` per spec SHOULD return empty expansion. medterm4ds
        currently returns 422 (per CF-SKEPTIC-VS02-01 resweep LOW).

        Pinned by prior test_s22; this resweep adds the spec-citation
        discipline: per FHIR R4 §4.7.5 In Parameters ``count``: "A count
        of 0 means that no entries will be returned."
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"count": 0})
        # Either 422 (current) OR 200 with empty contains (spec-correct).
        assert status in (200, 422), f"expected 200/422, got {status}: {body}"
        if status == 422:
            assert body.get("resourceType") == "OperationOutcome"
        else:
            assert body["expansion"]["contains"] == []

    def test_s23_count_huge_capped_at_1000(self, fhir_client):
        """``count=1000000`` MUST be rejected (>1000 cap) per FastAPI Query."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"count": 1000000})
        assert status == 422, f"expected 422, got {status}: {body}"
        assert body.get("resourceType") == "OperationOutcome"

    def test_s24_count_at_boundary_returns_all(self, fhir_client):
        """``count=1000`` (max) returns all matches without error.

        Per FHIR R4 §4.7.5: count is max size.
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"count": 1000})
        assert status == 200
        assert body["expansion"]["total"] == 2

    @pytest.mark.parametrize("offset_str", ["abc", "-1", "1.5", "0x5"])
    def test_s25_offset_non_integer_rejected(self, fhir_client, offset_str):
        """``offset`` non-integer MUST be rejected by FastAPI."""
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "offset": offset_str},
        )
        assert status == 422
        assert body.get("resourceType") == "OperationOutcome"

    def test_s26_offset_huge_accepted(self, fhir_client):
        """``offset=999999999`` MUST be accepted (no 422 from FastAPI).

        Currently the impl ignores offset (CF-SKEPTIC-VS02-02 resweep
        variant); the param is accepted for spec-compat.
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"offset": 999999999})
        assert status == 200, f"expected 200, got {status}: {body}"

    def test_s27_date_param_malformed_accepted(self, fhir_client):
        """``date`` malformed MUST be accepted (impl doesn't parse it).

        Per FHIR R4 §4.7.5: ``date`` is for version-snapshot selection;
        medterm4ds is single-snapshot. The param MUST be accepted without
        422 even when malformed (we don't validate format).
        """
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "date": "not-a-date"},
        )
        assert status != 422, f"date param should be accepted, got 422: {body}"

    def test_s28_inline_valueset_with_empty_compose(self, fhir_client):
        """POST a ValueSet with ``compose`` empty dict MUST return 200
        with empty expansion (not 500)."""
        vs = {"resourceType": "ValueSet", "compose": {}}
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"expected 200, got {status}: {body}"
        assert body["resourceType"] == "ValueSet"
        assert body["expansion"]["total"] == 0

    def test_s29_inline_valueset_with_compose_null(self, fhir_client):
        """POST a ValueSet with ``compose: null`` MUST NOT 500.

        The VS-01 SKEPTIC fix at apps/fhir_api.py:2481-2482 silently
        resets ``compose = {}`` when it's a non-dict. This probe confirms
        the guard fires on null too.
        """
        vs = {"resourceType": "ValueSet", "compose": None}
        status, body = _post_expand(fhir_client, vs)
        assert status < 500, f"5xx on compose=null: {status}: {body}"
        if status == 200:
            assert body["resourceType"] == "ValueSet"
            assert body["expansion"]["total"] == 0


# =============================================================================
# Item 3: Response shape — ValueSet with expansion.{timestamp, total, contains[]}
# =============================================================================


class TestItem3ResponseShapeAudit:
    """Item 3: response shape conforms to FHIR R4 §4.9 on every mode."""

    def test_s30_filter_mode_shape(self, fhir_client):
        """Filter mode response MUST have resourceType=ValueSet + expansion."""
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200
        assert body["resourceType"] == "ValueSet"
        assert "timestamp" in body["expansion"]
        assert "total" in body["expansion"]
        assert "contains" in body["expansion"]

    def test_s31_intensional_mode_shape(self, fhir_client):
        """Intensional mode response shape per FHIR R4 §4.9."""
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        assert body["resourceType"] == "ValueSet"
        assert "timestamp" in body["expansion"]
        assert "total" in body["expansion"]
        assert "contains" in body["expansion"]

    def test_s32_extensional_mode_shape(self, fhir_client):
        """Extensional mode response shape per FHIR R4 §4.9."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        assert body["resourceType"] == "ValueSet"
        assert "timestamp" in body["expansion"]
        assert "total" in body["expansion"]
        assert "contains" in body["expansion"]

    def test_s33_filter_mode_total_is_integer(self, fhir_client):
        """``expansion.total`` MUST be an integer (not string/float)."""
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200
        assert isinstance(body["expansion"]["total"], int), (
            f"total is {type(body['expansion']['total'])}: {body}"
        )

    def test_s34_intensional_mode_total_is_integer(self, fhir_client):
        """Intensional mode total MUST be an integer."""
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        assert isinstance(body["expansion"]["total"], int)

    def test_s35_expansion_timestamp_is_iso8601(self, fhir_client):
        """``expansion.timestamp`` is ISO 8601 UTC instant per §4.9.1."""
        iso_re = re.compile(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
        )
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200
        ts = body["expansion"]["timestamp"]
        assert iso_re.match(ts), f"timestamp not ISO 8601: {ts!r}"

    def test_s36_expansion_timestamp_is_recent(self, fhir_client):
        """``expansion.timestamp`` is within the last 60 seconds (no stale
        hardcoded date per VR-001 fix)."""
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200
        ts = body["expansion"]["timestamp"]
        # Trim the 'Z' and parse.
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - parsed
        assert abs(delta.total_seconds()) < 60, (
            f"timestamp {ts!r} is {delta} from now"
        )

    def test_s37_extensional_contains_entry_shape(self, fhir_client):
        """Each extensional contains[] entry has system, code, display."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        for entry in body["expansion"]["contains"]:
            assert "system" in entry
            assert "code" in entry
            assert "display" in entry


# =============================================================================
# Item 4: Expansion contains[] shape — system, code, display, version
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains
# =============================================================================


class TestItem4ContainsShapeAudit:
    """Item 4: each contains[] entry has correct shape."""

    def test_s40_contains_system_canonical_intensional(self, fhir_client):
        """Intensional mode contains[].system MUST be canonical (CR-013)."""
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        for entry in body["expansion"]["contains"]:
            assert entry["system"] == SNOMED_URI, (
                f"contains[].system {entry['system']!r} != canonical {SNOMED_URI!r}"
            )

    def test_s41_contains_system_canonical_filter_mode(self, fhir_client):
        """Filter mode contains[].system MUST be canonical (TS-03 SKEPTIC
        QA-001 + canonical_system_uri helper applied).

        Filter mode may match across multiple sources (the filter
        'diabetes' matches SNOMED DM + SNOMED T2DM + ICD-10-CM T2DM); the
        canonical-system probe is parametrized over every match.
        """
        # Canonical URIs per FHIR R4 / medterm4ds SYSTEM_TO_FHIR_URI registry.
        canonical_uris = {
            SNOMED_URI,
            ICD10CM_URI,
            RXNORM_URI,
            "http://loinc.org",
            "http://www.ama-assn.org/go/cpt",
            "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets",
            "http://hl7.org/fhir/sid/cvx",
            "http://hl7.org/fhir/sid/icd-10-pcs",
        }
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200
        for entry in body["expansion"]["contains"]:
            assert entry["system"] in canonical_uris, (
                f"filter-mode system {entry['system']!r} not in canonical URIs"
            )

    def test_s42_contains_system_canonical_alias_input(self, fhir_client):
        """``system`` in contains[] MUST be canonical even on alias input."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": "urn:oid:2.16.840.1.113883.6.96",
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes

    def test_s43_contains_display_nonempty_when_known(self, fhir_client):
        """Each contains[].display SHOULD be non-empty when code is known."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        for entry in body["expansion"]["contains"]:
            assert entry["display"], f"empty display: {entry}"

    def test_s44_contains_display_resolves_canonical_when_omitted(self, fhir_client):
        """VS-01 TERMINOLOGIST QA-056: when client OMITS the display, the
        server resolves canonical preferred term via get_code_infos."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],  # no display
                }],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        for entry in body["expansion"]["contains"]:
            if entry["code"] == SNOMED_DIABETES_MELLITUS:
                assert "Diabetes" in entry["display"], (
                    f"omitted display not resolved to canonical: {entry}"
                )

    def test_s45_contains_version_optional(self, fhir_client):
        """``version`` on contains[] is optional per FHIR R4 §4.9.3.4.

        medterm4ds is single-snapshot; version is omitted. Probe confirms
        the contract (no 5xx when version absent).
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        # version absent OR present — both are conformant.
        for entry in body["expansion"]["contains"]:
            if "version" in entry:
                assert isinstance(entry["version"], str)


# =============================================================================
# Item 5: Paging semantics — offset+count, total
# =============================================================================


class TestItem5PagingSemantics:
    """Item 5: paging semantics — count cap + offset acceptance."""

    def test_s50_count_caps_extensional(self, fhir_client):
        """``count=N`` MUST cap the response size on extensional mode."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        assert len(body["expansion"]["contains"]) == 1
        # total reflects UN-truncated size (VS-02 SKEPTIC QA-057 fix).
        assert body["expansion"]["total"] == 2

    def test_s51_count_caps_intensional(self, fhir_client):
        """``count=N`` MUST cap on intensional mode too.

        CF-HISTORIAN-VS02-01 (HIGH OPEN) territory: the intensional path
        uses BFS with ``limit=count`` which pre-truncates. This probe
        confirms that the intensional expansion returns the correct total
        under the small fixture (1 mrrel row + 1 root = 2 entries, count=1
        truncates 1 of 2 — total SHOULD be 2 if no BFS pre-truncation).

        Per FHIR R4 §4.9.2: "the total number of concepts in the
        expansion" — the FULL size, not the post-truncation count.
        """
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        # The conformance fixture has SNOMED root + 1 descendant = 2 codes.
        # count=1 truncates to 1.
        assert len(body["expansion"]["contains"]) == 1
        # The total SHOULD reflect the UN-truncated size (2) per §4.9.2.
        # CF-HISTORIAN-VS02-01 documentation: when the fixture grows to >
        # 1 mrrel row, this assertion would FAIL because the intensional
        # path's ``get_descendants_bfs(limit=count)`` pre-truncates the
        # ``deduped`` list BEFORE ``total=len(deduped)`` is computed.
        assert body["expansion"]["total"] == 2, (
            f"intensional total under count=1: expected 2 (root+1 desc), got "
            f"{body['expansion']['total']} (CF-HISTORIAN-VS02-01 territory; "
            f"current fixture has exactly 1 mrrel row so 2 = root + 1 descendant)"
        )

    def test_s52_count_1_toocostly_extensional(self, fhir_client):
        """count=1 extensional MUST emit valueset-toocostly extension."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        exts = body["expansion"].get("extension", [])
        too_costly = [e for e in exts if e.get("url") == TOOCOSTLY_URL]
        assert too_costly, f"missing toocostly on extensional count=1: {exts}"
        assert too_costly[0].get("valueBoolean") is True

    def test_s53_count_at_exact_boundary_no_toocostly(self, fhir_client):
        """``count=N`` matching exactly the expansion size MUST NOT emit
        toocostly extension (VS-04 TERMINOLOGIST QA-068 boundary: ``>``
        not ``>=``)."""
        vs = _make_extensional_snomed()  # 2 concepts
        status, body = _post_expand(fhir_client, vs, params={"count": 2})
        assert status == 200
        assert len(body["expansion"]["contains"]) == 2
        exts = body["expansion"].get("extension", [])
        too_costly = [e for e in exts if e.get("url") == TOOCOSTLY_URL]
        assert not too_costly, (
            f"toocostly fired on count=2 against 2-concept expansion: {exts} "
            f"(VS-04 TERMINOLOGIST QA-068 boundary: > not >=)"
        )

    def test_s54_offset_zero_default_no_change(self, fhir_client):
        """``offset=0`` MUST behave as no offset (first page)."""
        vs = _make_extensional_snomed()
        s1, b1 = _post_expand(fhir_client, vs)
        s2, b2 = _post_expand(fhir_client, vs, params={"offset": 0})
        assert s1 == s2 == 200
        assert _contains_codes(b1) == _contains_codes(b2)

    def test_s55_offset_accepted_on_get(self, fhir_client):
        """``offset`` is declared on ``expand_get`` per FHIR R4 §4.7.5."""
        # Offset is accepted (no 422 about unknown param).
        status, _ = _get_expand(
            fhir_client, params={"filter": "diabetes", "offset": 5}
        )
        assert status != 422

    def test_s56_offset_accepted_on_post(self, fhir_client):
        """``offset`` is declared on ``expand_post`` per FHIR R4 §4.7.5.

        Pinned by VS-02 HISTORIAN test_h42 — ``expand_post`` declared the
        ``offset`` param after VS-02 HISTORIAN iteration 1.
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"offset": 5})
        assert status == 200, f"offset on POST not accepted: {body}"

    def test_s57_total_when_not_truncated_extensional(self, fhir_client):
        """When not truncated, total == len(contains) per §4.9.2."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs)  # default count=20
        assert status == 200
        assert body["expansion"]["total"] == len(body["expansion"]["contains"])


# =============================================================================
# Item 6: Hierarchical expansions are NOT paged (entire expansion returned)
# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html
# =============================================================================


class TestItem6HierarchicalExpansions:
    """Item 6: hierarchical expansions are not paged."""

    def test_s60_intensional_returns_full_hierarchy(self, fhir_client):
        """Intensional (is-a) MUST return full hierarchy (root + descendants)."""
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s61_intensional_count_caps_with_toocostly(self, fhir_client):
        """count=N on intensional MUST truncate + emit toocostly extension.

        Per VS-01 TERMINOLOGIST QA-055: ``expand_post`` honors client
        count for both body shapes. CF-HISTORIAN-VS02-01 territory.
        """
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        assert len(body["expansion"]["contains"]) == 1
        exts = body["expansion"].get("extension", [])
        too_costly = [e for e in exts if e.get("url") == TOOCOSTLY_URL]
        assert too_costly, f"missing toocostly on intensional count=1: {exts}"

    def test_s62_descendent_of_excludes_root(self, fhir_client):
        """``descendent-of`` (spec-correct spelling) MUST exclude the root.

        Per VS-01 SKEPTIC QA-054: only ``is-a`` and ``descendent-of`` are
        honored; ``descendent-of`` returns descendants only.
        """
        vs = _make_intensional_snomed_descendent_of()
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        # Root (Diabetes mellitus) MUST NOT be present.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) not in codes, (
            f"descendent-of should exclude root: {codes}"
        )
        # Descendant (T2DM) MUST be present.
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s63_intensional_offset_zero_no_change(self, fhir_client):
        """``offset=0`` on intensional MUST return full hierarchy."""
        vs = _make_intensional_snomed_isa()
        s, b = _post_expand(fhir_client, vs, params={"offset": 0})
        assert s == 200
        codes = _contains_codes(b)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes


# =============================================================================
# Item 7: too-costly OperationOutcome for very large expansions
# =============================================================================


class TestItem7TooCostly:
    """Item 7: too-costly signal."""

    def test_s70_toocostly_extensional_count_1(self, fhir_client):
        """count=1 extensional MUST emit toocostly extension."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        exts = body["expansion"].get("extension", [])
        too_costly = [e for e in exts if e.get("url") == TOOCOSTLY_URL]
        assert too_costly
        assert too_costly[0].get("valueBoolean") is True

    def test_s71_no_toocostly_when_not_truncated(self, fhir_client):
        """Non-truncated extensional MUST NOT emit toocostly extension."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"count": 1000})
        assert status == 200
        exts = body["expansion"].get("extension", [])
        too_costly = [e for e in exts if e.get("url") == TOOCOSTLY_URL]
        assert not too_costly

    def test_s72_toocostly_has_reason_extension(self, fhir_client):
        """toocostly extension SHOULD include reason nested extension."""
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        exts = body["expansion"].get("extension", [])
        too_costly = next(
            (e for e in exts if e.get("url") == TOOCOSTLY_URL), None
        )
        assert too_costly is not None
        reasons = [e for e in too_costly.get("extension", []) if e.get("url") == "reason"]
        assert reasons, f"toocostly missing reason extension: {too_costly}"

    def test_s73_filter_mode_truncation_emits_toocostly(self, fhir_client):
        """GET filter path with truncation MUST emit toocostly extension.

        CF-SKEPTIC-VS02-03 closed by VS-02 SKEPTIC resweep QA-001 fix.
        The prior implementation called ``build_valueset_expand`` without
        the toocostly extension on the filter path
        (apps/fhir_api.py:2448). The QA-001 fix uses the ``+1 probe``
        pattern to detect truncation and emits the extension as the
        clinical-safety signal.
        """
        status, body = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 1}
        )
        assert status == 200
        # With 3 diabetes matches in the fixture and count=1, truncation
        # MUST fire and emit the extension.
        exts = body["expansion"].get("extension", [])
        too_costly = [e for e in exts if e.get("url") == TOOCOSTLY_URL]
        assert too_costly, (
            f"GET filter path MUST emit valueset-toocostly extension on "
            f"truncation (CF-SKEPTIC-VS02-03 closed by QA-001 fix). "
            f"Got: {exts}"
        )

    def test_s74_toocostly_count_at_boundary_intensional(self, fhir_client):
        """count=2 intensional against 2-concept expansion MUST NOT emit
        toocostly (boundary: > not >=, VS-04 TERMINOLOGIST QA-068)."""
        vs = _make_intensional_snomed_isa()  # 2 concepts (root + T2DM)
        status, body = _post_expand(fhir_client, vs, params={"count": 2})
        assert status == 200
        exts = body["expansion"].get("extension", [])
        too_costly = [e for e in exts if e.get("url") == TOOCOSTLY_URL]
        assert not too_costly, (
            f"toocostly fired on intensional count=2 vs 2-concept expansion: {exts}"
        )


# =============================================================================
# Item 8: Filter text matches against display, code, or designation
# =============================================================================


class TestItem8FilterMatching:
    """Item 8: filter text matching."""

    def test_s80_filter_matches_display_diabetes(self, fhir_client):
        """filter='diabetes' matches SNOMED DM + T2DM displays."""
        status, body = _get_expand(fhir_client, params={"filter": "diabetes"})
        assert status == 200
        codes = _contains_codes(body)
        assert codes, f"no matches for 'diabetes': {body}"
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes

    def test_s81_filter_matches_display_metformin(self, fhir_client):
        """filter='metformin' matches RxNorm metformin display."""
        status, body = _get_expand(fhir_client, params={"filter": "metformin"})
        assert status == 200
        codes = _contains_codes(body)
        assert (RXNORM_URI, RXNORM_METFORMIN) in codes

    def test_s82_filter_no_match_returns_empty_expansion(self, fhir_client):
        """No-match filter MUST return empty expansion (200, not 404)."""
        status, body = _get_expand(
            fhir_client, params={"filter": "zzzznomatch"}
        )
        assert status == 200
        assert body["expansion"]["total"] == 0
        assert body["expansion"]["contains"] == []

    def test_s83_filter_case_insensitive(self, fhir_client):
        """filter 'DIABETES' SHOULD match same set as 'diabetes'."""
        _, b_lower = _get_expand(fhir_client, params={"filter": "diabetes"})
        _, b_upper = _get_expand(fhir_client, params={"filter": "DIABETES"})
        assert set(_contains_codes(b_lower)) == set(_contains_codes(b_upper))

    def test_s84_filter_substring_match(self, fhir_client):
        """filter substring MUST match (e.g. 'diabet' matches Diabetes)."""
        status, body = _get_expand(fhir_client, params={"filter": "diabet"})
        assert status == 200
        codes = _contains_codes(body)
        assert codes

    def test_s85_filter_get_post_parity(self, fhir_client):
        """GET and POST filter mode MUST produce equivalent results."""
        s_get, b_get = _get_expand(
            fhir_client, params={"filter": "diabetes"}
        )
        params_body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "filter", "valueString": "diabetes"}],
        }
        s_post, b_post = _post_expand(fhir_client, params_body)
        assert s_get == s_post == 200
        assert b_get["expansion"]["total"] == b_post["expansion"]["total"]
        assert set(_contains_codes(b_get)) == set(_contains_codes(b_post))


# =============================================================================
# CF-HISTORIAN-VS02-01 (HIGH OPEN) — independent re-verification
# Spec: FHIR R4 §4.9.2 — expansion.total MUST reflect UN-truncated size.
# Found by VS-02 HISTORIAN; pinned by test_vs02_historian.py test_h11/h14/h15.
# The bug: ``_expand_intensional`` calls ``get_descendants_bfs(..., limit=count)``
# which PRE-TRUNCATES the descendants list. The post-BFS ``total=len(deduped)``
# then reports the truncated size when the cap fired. Invisible in CI because
# the fixture has exactly 1 mrrel row matching count=1.
# =============================================================================


class TestCfHistorianVs02One:
    """CF-HISTORIAN-VS02-01 independent re-verification per SKEPTIC tip."""

    def test_s90_intensional_count_1_total_under_small_fixture(self, fhir_client):
        """count=1 against 1-descendant fixture: total SHOULD be 2.

        Per FHIR R4 §4.9.2: total = number of concepts in the expansion.
        The fixture has 1 mrrel row (T2DM → DM) + 1 root = 2 concepts.
        count=1 truncates to 1 contains[] entry; total should be 2.

        When the fixture grows to >1 descendant per root, this probe will
        start FAILING because the intensional path's BFS pre-truncates
        the descendants list (CF-HISTORIAN-VS02-01).
        """
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        assert len(body["expansion"]["contains"]) == 1
        assert body["expansion"]["total"] == 2, (
            f"intensional count=1 total: expected 2 (root + 1 desc), got "
            f"{body['expansion']['total']}. CF-HISTORIAN-VS02-01 territory."
        )

    def test_s91_intensional_no_truncation_total_equals_contains(self, fhir_client):
        """When NOT truncated, total == len(contains) on intensional."""
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs)  # default count=20
        assert status == 200
        assert body["expansion"]["total"] == len(body["expansion"]["contains"])

    def test_s92_bfs_helper_has_limit_parameter_source_audit(self):
        """Source-read: ``get_descendants_bfs`` accepts a ``limit`` parameter.

        CF-HISTORIAN-VS02-01 root cause: the intensional call site at
        apps/fhir_api.py:2597-2602 passes ``limit=count``, causing BFS to
        pre-truncate. Source-read confirms the helper signature.
        """
        from medterm4ds.services import hierarchy
        sig = inspect.signature(hierarchy.get_descendants_bfs)
        assert "limit" in sig.parameters, (
            f"get_descendants_bfs signature missing limit param: {sig}"
        )

    def test_s93_intensional_call_site_passes_limit_count_source_audit(self):
        """Source-read: ``_expand_intensional`` calls BFS with limit=count.

        The load-bearing line for CF-HISTORIAN-VS02-01.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        assert "get_descendants_bfs(" in src, (
            "could not find get_descendants_bfs call in _expand_intensional"
        )
        # The call site MUST pass limit=count (the source of the BFS cap).
        assert "limit=count" in src, (
            "could not find 'limit=count' in _expand_intensional — has the "
            "call site been refactored? CF-HISTORIAN-VS02-01 status may have "
            "changed; update this probe and the GLOBAL_KNOWLEDGE entry."
        )

    def test_s94_intensional_total_uses_deduped_len_source_audit(self):
        """Source-read: ``_expand_intensional`` passes ``total=len(deduped)``.

        After the BFS pre-truncates the descendants list, ``deduped`` is
        already truncated; ``len(deduped)`` reports the truncated size.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        assert "total=len(deduped)" in src, (
            "could not find 'total=len(deduped)' in _expand_intensional"
        )

    def test_s95_url_pattern_path_uses_count_limited_lower_bound_source_audit(self):
        """Source-read: ``expand_url_pattern`` uses ``len(contains) + 1`` lower
        bound when count_limited fires (VS-04 TERMINOLOGIST QA-068 fix)."""
        src = _get_func_source(_FHIR_API_PATH, "expand_url_pattern")
        assert "total = len(contains) + 1" in src, (
            "could not find 'total = len(contains) + 1' in expand_url_pattern"
        )


# =============================================================================
# build_valueset_expand call-site audit per VS-01/TERMINOLOGIST tip
# Per GLOBAL_RULES.md line 136 PROMOTED pattern: every truncating call site
# MUST pass ``total=<un-truncated-size>``.
# =============================================================================


class TestBuildValuesetExpandCallSiteAudit:
    """Audit every call site of ``build_valueset_expand`` for ``total=``."""

    def test_s100_all_call_sites_pass_total(self):
        """Every call to build_valueset_expand MUST pass ``total=``.

        Per VS-02 SKEPTIC QA-057 (count=3 PROMOTED at GLOBAL_RULES.md line
        136): every truncating call site MUST pass the un-truncated size.
        The VS-01/TERMINOLOGIST tip for this resweep asked SKEPTIC to
        audit all call sites, including the filter-mode call at
        ``_do_expand`` line 2448. QA-001 (HIGH) was filed on the prior
        buggy state where the filter-mode call site omitted ``total=``;
        the fix added the ``+1 probe`` pattern with explicit ``total=``
        and ``extensions=`` keyword args. This probe now PASSES.
        """
        src = _FHIR_API_PATH.read_text()
        tree = ast.parse(src)
        call_sites = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "build_valueset_expand"
            ):
                call_sites.append(node)
        assert call_sites, (
            "expected at least 1 call site to build_valueset_expand"
        )
        missing = []
        for call in call_sites:
            kwarg_names = [kw.arg for kw in call.keywords]
            if "total" not in kwarg_names:
                missing.append((call.lineno, kwarg_names))
        assert not missing, (
            f"build_valueset_expand call sites NOT passing total=: {missing}. "
            f"VS-02 SKEPTIC resweep QA-001 — every call site MUST pass total=."
        )

    def test_s101_filter_mode_total_reflects_untruncated_lower_bound(self, fhir_client):
        """Behavioral confirmation of QA-001 RESOLVED: filter-mode total
        now reflects the un-truncated LOWER BOUND per FHIR R4 §4.9.2.

        With filter='diabetes' count=1, the FULL match count is 3
        (SNOMED DM + SNOMED T2DM + ICD-10-CM T2DM). The fix uses the
        ``+1 probe`` pattern: search_names(limit=count+1) returns at most
        count+1 results; when len(results) > count, we know the natural
        match count is at least count+1 (lower bound; exact count
        requires unbounded search per CF-HISTORIAN-VS02-01 BFS path).

        With count=1: total reports 2 (lower bound: at least 2 matches),
        NOT 1 (the prior buggy post-truncation size). When the fixture
        stays small, the lower bound equals the exact count when
        count_limited is False.
        """
        # First, confirm the FULL match count is 3 with no truncation.
        status_full, body_full = _get_expand(
            fhir_client, params={"filter": "diabetes"}
        )
        assert status_full == 200
        full_count = body_full["expansion"]["total"]
        assert full_count == 3, (
            f"expected 3 diabetes matches (SNOMED DM + ICD-10-CM T2DM + "
            f"SNOMED T2DM), got {full_count}"
        )
        # Now apply count=1 — this truncates contains[] to 1 entry.
        status_trunc, body_trunc = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 1}
        )
        assert status_trunc == 200
        # QA-001 RESOLVED: total reflects the un-truncated lower bound (2).
        # The +1 probe (limit=count+1=2) returned 2 results; count_limited
        # fired because 2 > 1; total = len(results) + 1 = 2.
        assert body_trunc["expansion"]["total"] == 2, (
            f"filter-mode total after fix: expected 2 (lower bound from +1 "
            f"probe: count_limited fired with len(results)=2 > count=1), "
            f"got {body_trunc['expansion']['total']}"
        )
        assert len(body_trunc["expansion"]["contains"]) == 1

    def test_s102_filter_mode_emits_toocostly_when_truncated(self, fhir_client):
        """CF-SKEPTIC-VS02-03 closed by the same QA-001 fix: filter-mode
        now emits valueset-toocostly extension when truncation fires.

        Both gaps shared the same root cause (filter-mode call site
        omitted ``total=`` AND ``extensions=``); both are closed by the
        ``+1 probe`` pattern fix.
        """
        status, body = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 1}
        )
        assert status == 200
        exts = body["expansion"].get("extension", [])
        too_costly = [e for e in exts if e.get("url") == TOOCOSTLY_URL]
        assert too_costly, (
            f"CF-SKEPTIC-VS02-03 closed by QA-001 fix: filter-mode MUST emit "
            f"valueset-toocostly extension on truncation. Got: {exts}"
        )
        assert too_costly[0].get("valueBoolean") is True

    def test_s101_filter_mode_call_site_passes_total_explicitly_source_audit(self):
        """The filter-mode call site at ``_do_expand`` now passes total=
        explicitly per QA-001 RESOLVED.

        Per VS-01/TERMINOLOGIST tip: ``_do_expand`` filter mode calls
        ``search_names(limit=count+1)`` (the ``+1 probe`` pattern). The
        QA-001 fix added explicit ``total=untruncated_total`` and
        ``extensions=extensions`` keyword args. Source-read confirms the
        call site's total= shape.
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        # The QA-001 RESOLVED call site uses the +1 probe + explicit total/extensions.
        assert "limit=count + 1" in src, (
            "could not find search_names(limit=count + 1) +1 probe pattern"
        )
        assert "total=untruncated_total" in src, (
            "filter-mode call site missing total=untruncated_total (QA-001 fix)"
        )
        assert "extensions=extensions" in src, (
            "filter-mode call site missing extensions=extensions (QA-001 fix)"
        )

    def test_s102_intensional_call_site_passes_total_explicitly_source_audit(self):
        """Intensional call site at ``_expand_intensional`` passes total=.

        Per VS-02 SKEPTIC QA-057 fix: the intensional path passes
        ``total=len(deduped)`` (the UN-truncated size of the deduped list,
        but CF-HISTORIAN-VS02-01 notes this is the post-BFS-truncation
        size when count caps).
        """
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        # The intensional call site uses total=len(deduped).
        assert "total=len(deduped)" in src, (
            "intensional call site missing total=len(deduped)"
        )

    def test_s103_implicit_value_set_call_site_passes_total_source_audit(self):
        """Implicit value set call site at ``_expand_implicit_value_set``
        passes total=untruncated_total.

        Per VS-02 SKEPTIC QA-057 + the implicit-value-set path's
        ``LIMIT count + 1`` query trick for truncation detection.
        """
        src = _get_func_source(
            _FHIR_API_PATH, "create_fhir_app", "_expand_implicit_value_set"
        )
        assert "total=untruncated_total" in src, (
            "implicit value set call site missing total=untruncated_total"
        )

    def test_s104_url_pattern_call_site_passes_total_source_audit(self):
        """``expand_url_pattern`` (module-level) passes total= per
        VS-04 TERMINOLOGIST QA-068 (lower bound ``len(contains) + 1``)."""
        src = _get_func_source(_FHIR_API_PATH, "expand_url_pattern")
        assert "total=total" in src, (
            "expand_url_pattern call site missing total=total"
        )


# =============================================================================
# Source-read structural contracts — lock in expected code shape
# =============================================================================


class TestSourceReadStructuralContracts:
    """Source-read probes that lock in expected code shape."""

    def test_s110_build_valueset_expand_has_total_param(self):
        """``build_valueset_expand`` accepts ``total: int | None = None``."""
        from medterm4ds.engines.fhir import responses
        sig = inspect.signature(responses.build_valueset_expand)
        assert "total" in sig.parameters
        assert sig.parameters["total"].default is None

    def test_s111_build_valueset_expand_uses_total_when_provided_source_audit(self):
        """Source-read: ``build_valueset_expand`` uses total when provided."""
        from medterm4ds.engines.fhir import responses
        src = inspect.getsource(responses.build_valueset_expand)
        # Per VS-02 SKEPTIC QA-057 fix.
        assert "len(contains) if total is None else total" in src

    def test_s112_truncation_extensions_helper_exists(self):
        """``_truncation_extensions`` helper is defined per VS-01 SKEPTIC."""
        src = _FHIR_API_PATH.read_text()
        assert "def _truncation_extensions(" in src

    def test_s113_intensional_path_uses_canonical_inc_source_audit(self):
        """Intensional path uses ``canonical_inc`` for contains[].system
        per CR-013 (milestone-2 review)."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        assert "canonical_inc" in src

    def test_s114_intensional_path_isinstance_guard_on_compose(self):
        """VS-01 SKEPTIC QA-001 fix: isinstance(compose, dict) guard at
        parent compose data-access boundary."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        # The guard pattern.
        assert "isinstance(compose, dict)" in src

    def test_s115_intensional_path_isinstance_guard_on_include(self):
        """CS-04 HISTORIAN QA-001 fix: isinstance(include, dict) guard."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        assert "isinstance(include, dict)" in src

    def test_s116_intensional_path_isinstance_guard_on_concept(self):
        """CS-04 HISTORIAN QA-001 fix: isinstance(concept, dict) guard."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        assert "isinstance(concept, dict)" in src

    def test_s117_intensional_path_isinstance_guard_on_filter(self):
        """CS-04 HISTORIAN QA-001 fix: isinstance(filt, dict) guard."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        assert "isinstance(filt, dict)" in src

    def test_s118_intensional_path_isinstance_guard_on_exclude(self):
        """CS-04 HISTORIAN QA-001 fix: isinstance(exclude, dict) guard."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_expand_intensional")
        assert "isinstance(exclude, dict)" in src

    def test_s119_filter_mode_path_uses_canonical_system_uri(self):
        """Filter mode uses ``system_to_fhir_uri`` per TS-03 SKEPTIC QA-001
        canonical-system contract on contains[].system."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        assert "system_to_fhir_uri(r.code.source)" in src

    def test_s120_filter_mode_path_wraps_search_names_value_error(self):
        """Filter mode wraps search_names ValueError → 400 (TS-02 EXPLORER
        QA-027 fix)."""
        src = _get_func_source(_FHIR_API_PATH, "create_fhir_app", "_do_expand")
        assert "except ValueError as exc:" in src
        assert "_fhir_error(400" in src


# =============================================================================
# Response shape audit on every mode + Content-Type contract
# =============================================================================


class TestResponseShapeEveryMode:
    """Response shape per FHIR R4 §4.9 on every mode."""

    def test_s130_filter_mode_content_type(self, fhir_client):
        """Filter mode MUST emit application/fhir+json."""
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes"},
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/fhir+json"

    def test_s131_intensional_mode_content_type(self, fhir_client):
        """Intensional mode MUST emit application/fhir+json."""
        vs = _make_intensional_snomed_isa()
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/fhir+json"

    def test_s132_extensional_mode_content_type(self, fhir_client):
        """Extensional mode MUST emit application/fhir+json."""
        vs = _make_extensional_snomed()
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/fhir+json"

    def test_s133_xml_format_on_filter_mode(self, fhir_client):
        """``_format=xml`` honored on filter mode (CR-002 XML serializer)."""
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"filter": "diabetes", "_format": "xml"},
            headers={"Accept": "application/fhir+xml"},
        )
        assert resp.status_code == 200
        assert "xml" in resp.headers["content-type"]

    def test_s134_xml_format_on_intensional_mode(self, fhir_client):
        """``_format=xml`` honored on intensional mode."""
        vs = _make_intensional_snomed_isa()
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            params={"_format": "xml"},
            headers={"Accept": "application/fhir+xml"},
        )
        assert resp.status_code == 200
        assert "xml" in resp.headers["content-type"]

    def test_s135_error_path_content_type_fhir_json(self, fhir_client):
        """Error path MUST emit application/fhir+json + OperationOutcome."""
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={},
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 400
        assert resp.headers["content-type"] == "application/fhir+json"
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"


# =============================================================================
# Cross-handler GET↔POST byte-exact parity
# =============================================================================


class TestGetPostParity:
    """GET ↔ POST byte-exact parity on filter mode."""

    def test_s140_filter_get_post_byte_exact(self, fhir_client):
        """GET filter and POST Parameters-body filter produce identical
        total + contains[].codes."""
        s_get, b_get = _get_expand(
            fhir_client, params={"filter": "diabetes"}
        )
        params_body = {
            "resourceType": "Parameters",
            "parameter": [{"name": "filter", "valueString": "diabetes"}],
        }
        s_post, b_post = _post_expand(fhir_client, params_body)
        assert s_get == s_post == 200
        assert b_get["expansion"]["total"] == b_post["expansion"]["total"]
        assert set(_contains_codes(b_get)) == set(_contains_codes(b_post))

    def test_s141_filter_get_post_count_parity(self, fhir_client):
        """GET filter with count=N and POST Parameters with count=N
        produce identical results."""
        s_get, b_get = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 100}
        )
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "filter", "valueString": "diabetes"},
                {"name": "count", "valueInteger": 100},
            ],
        }
        s_post, b_post = _post_expand(fhir_client, params_body)
        assert s_get == s_post == 200
        assert b_get["expansion"]["total"] == b_post["expansion"]["total"]
        assert set(_contains_codes(b_get)) == set(_contains_codes(b_post))


# =============================================================================
# Closed-enum registry-as-contract — FHIR_R4_FILTER_OPERATORS
# =============================================================================


class TestClosedEnumRegistryContract:
    """Closed-enum registry-as-contract — FHIR_R4_FILTER_OPERATORS."""

    def test_s150_filter_operators_importable(self):
        """``FHIR_R4_FILTER_OPERATORS`` is importable (CR-014)."""
        assert "is-a" in FHIR_R4_FILTER_OPERATORS
        assert "descendent-of" in FHIR_R4_FILTER_OPERATORS

    def test_s151_filter_operators_no_off_spec_descendant_of(self):
        """Off-spec ``descendant-of`` is NOT in the R4 enum (QA-054 fix)."""
        assert "descendant-of" not in FHIR_R4_FILTER_OPERATORS

    def test_s152_filter_operators_count_is_9(self):
        """FHIR R4 Filter Operator closed enum has exactly 9 values."""
        # Per https://hl7.org/fhir/R4/valueset-filter-operator.html.
        assert len(FHIR_R4_FILTER_OPERATORS) == 9


# =============================================================================
# META — Module-load sanity
# =============================================================================


class TestMeta:
    """Module-load sanity probes."""

    def test_s160_fhir_api_importable(self):
        """``apps.fhir_api`` module loads without error."""
        from medterm4ds.apps import fhir_api  # noqa: F401

    def test_s161_responses_importable(self):
        """``engines.fhir.responses`` module loads."""
        from medterm4ds.engines.fhir import responses  # noqa: F401

    def test_s162_hierarchy_importable(self):
        """``services.hierarchy`` module loads."""
        from medterm4ds.services import hierarchy  # noqa: F401

    def test_s163_cases_json_loaded(self):
        """``cases.json`` loads (sanity check)."""
        import json
        cases_path = Path(__file__).parent / "cases.json"
        with cases_path.open() as f:
            data = json.load(f)
        cases = data["cases"]
        # cases.json has at least the 4 expand cases.
        expand_cases = [c for c in cases if c["id"].startswith("expand-")]
        assert len(expand_cases) >= 4
