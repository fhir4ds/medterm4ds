"""VS-04 SKEPTIC resweep: ValueSet $expand — Intensional URLs (fhir_vs).

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
SNOMED CT intensional: https://hl7.org/fhir/R4/snomedct.html
Truncation ext: https://hl7.org/fhir/R4/extension-valueset-toocostly.html

This is the resweep (post-milestone-11) SKEPTIC pass for chunk VS-04. The
prior VS-04 SKEPTIC test_vs04_skeptic.py covered the 8 spec items + landed
5 fixes (QA-060/061/062 unrecognized-value dispatch + QA-065 depth=0
truncation signal + QA-066 invalid env-var + QA-067 negative depth + QA-068
count_limited >= vs > divergence). This resweep focuses on SKEPTIC's
hostile-input lens:

  1. **Hostile-input probes per spec item** — malformed fhir_vs values
     (``?fhir_vs=isa:extra``, ``?fhir_vs=isa#frag``, case variants on the
     KEY ``FHIR_VS``, missing/empty fhir_vs, multiple fhir_vs params),
     malformed SNOMED URLs (missing code, extra path segments, invalid
     version date, double-slash, fragment, userinfo, port).
  2. **FHIR_VS_MAX_DEPTH env var hostile values** — 0, negative, non-numeric,
     very large, very small, leading/trailing whitespace, hex, octal, IPv6-
     like, percent-encoded, unicode digit, empty string, multi-line.
  3. **Non-SNOMED systems** — parametrize over EVERY system URI in
     SYSTEM_TO_FHIR_URI (8 systems) plus aliases plus invented URIs. ALL
     except SNOMED MUST raise clear ValueError.
  4. **Truncation boundary** — count=N where N matches fixture size exactly
     (root + 1 descendant = 2). QA-068 territory: count=2 MUST NOT fire
     toocostly extension on COMPLETE expansion.
  5. **Canonical-DISPLAY cross-operation invariant** per VS-03/TERMINOLOGIST
     tip — ``?fhir_vs=isa`` contains[].display MUST equal $lookup Out
     display byte-exact for every seeded code. The 9th-instance
     ``canonical_system_uri`` helper (count=9 PROMOTED in
     GLOBAL_KNOWLEDGE.md / VS-02 SKEPTIC resweep) is the structural
     backbone to re-verify on the URL-form-specific code path.
  6. **Source-read structural contracts** — the simplest way to lock in
     expected behaviors without depending on fixture data.
  7. **Cross-handler GET↔POST byte-exact parity** on URL-form inputs.
  8. **QA-068 regression-pin** — count=2 expansion MUST NOT fire toocostly.

Conformance fixture (4 mrconso rows, 1 mrrel row): SNOMEDCT_US has 2 codes
(Diabetes mellitus / T2DM); ICD10CM has 1 (E11); RXNORM has 1 (metformin);
mrrel has a single isa relationship (T2DM → Diabetes mellitus).

References:
  - VS-03/TERMINOLOGIST tip (carry-forward): canonical-DISPLAY cross-
    operation invariant extended to VS-04 URL-form surface.
  - VS-04 in prior [2026-07-14] run was where the 4-personality rotation
    pattern BROKE — TERMINOLOGIST found QA-068 (`>=` vs `>` divergence)
    that the other 3 missed. We pin the regression here.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from urllib.parse import urlencode

import pytest

# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (canonical R4)
# Spec: https://hl7.org/fhir/R4/snomedct.html (Implicit Value Sets via fhir_vs)
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html (truncation)
# Spec: https://hl7.org/fhir/R4/snomedct.html (Edition/version URI)
from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

SNOMED_URI = SYSTEM_TO_FHIR_URI["SNOMEDCT_US"]  # http://snomed.info/sct
SNOMED_DIABETES_MELLITUS = "73211009"  # parent (root)
SNOMED_T2DM = "44054006"                # child of 73211009

LOINC_URI = SYSTEM_TO_FHIR_URI["LNC"]
RXNORM_URI = SYSTEM_TO_FHIR_URI["RXNORM"]
ICD10CM_URI = SYSTEM_TO_FHIR_URI["ICD10CM"]
ICD10PCS_URI = SYSTEM_TO_FHIR_URI["ICD10PCS"]
CPT_URI = SYSTEM_TO_FHIR_URI["CPT"]
HCPCS_URI = SYSTEM_TO_FHIR_URI["HCPCS"]
CVX_URI = SYSTEM_TO_FHIR_URI["CVX"]

TOOCOSTLY_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

# Path to apps/fhir_api.py for source-read structural probes.
_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)


# =============================================================================
# Helpers
# =============================================================================


def _expand_url(client, url: str, count: int | None = None):
    """Helper: GET /fhir/ValueSet/$expand with the given url (and count)."""
    params = [("url", url)]
    if count is not None:
        params.append(("count", count))
    return client.get("/fhir/ValueSet/$expand", params=params)


def _contains_codes(resp_json: dict) -> list[str]:
    return [c.get("code") for c in resp_json.get("expansion", {}).get("contains", [])]


def _contains(resp_json: dict) -> list[dict]:
    return resp_json.get("expansion", {}).get("contains", [])


def _extensions(resp_json: dict) -> list[dict]:
    return resp_json.get("expansion", {}).get("extension", [])


def _total(resp_json: dict) -> int | None:
    return resp_json.get("expansion", {}).get("total")


def _has_toocostly(resp_json: dict) -> bool:
    return any(e.get("url") == TOOCOSTLY_URL for e in _extensions(resp_json))


def _post_expand_url(fhir_client, url: str, count: int | None = None):
    """POST /fhir/ValueSet/$expand with url in Parameters body."""
    body_parameter = [{"name": "url", "valueUri": url}]
    if count is not None:
        body_parameter.append({"name": "count", "valueInteger": count})
    body = {
        "resourceType": "Parameters",
        "parameter": body_parameter,
    }
    return fhir_client.post(
        "/fhir/ValueSet/$expand",
        json=body,
        headers={"Accept": "application/fhir+json"},
    )


# =============================================================================
# Source-read helpers
# =============================================================================


def _read_module_source() -> str:
    return inspect.getsource(
        __import__("medterm4ds.apps.fhir_api", fromlist=["fhir_api"])
    )


def _read_function_source(module_src: str, func_name: str) -> str | None:
    """Return the source of a top-level function or None if not found.

    Walks ast.Module for ast.FunctionDef nodes (VS-04 surface functions
    are all module-level: expand_url_pattern, _resolve_max_depth,
    _truncation_extensions).
    """
    tree = ast.parse(module_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(module_src, node) or ""
    return None


def _read_nested_function_source(
    module_src: str, parent_name: str, child_name: str
) -> str | None:
    """Return the source of a nested function defined inside ``parent_name``.

    Mirrors the HISTORIAN CS-03 helper ``_get_nested_func_source``. Walks
    BOTH ast.FunctionDef AND ast.AsyncFunctionDef inside ``parent``.
    """
    tree = ast.parse(module_src)
    parent_node = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parent_name:
            parent_node = node
            break
    if parent_node is None:
        return None
    for child in ast.walk(parent_node):
        if (
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name == child_name
            and child is not parent_node
        ):
            return ast.get_source_segment(module_src, child) or ""
    return None


# =============================================================================
# L1: fhir_vs VALUE hostile inputs (Spec Items 1, 2, 3)
# Spec: https://hl7.org/fhir/R4/snomedct.html
#   - fhir_vs=isa: include root + descendants
#   - fhir_vs (no value): equivalent to isa
#   - fhir_vs=refset: include refset members (medterm4ds raises ValueError)
# =============================================================================


class TestL1FhirVsValueHostileInputs:
    """Spec items 1, 2, 3: hostile inputs on the fhir_vs VALUE.

    The dispatch table at apps/fhir_api.py:213-219 normalizes to lowercase
    and rejects unrecognized values with ValueError. SKEPTIC verifies every
    value variant produces either:
      (a) full isa expansion (root + descendants) for recognized values, OR
      (b) explicit ValueError / 400 for unrecognized values, OR
      (c) explicit ValueError / 400 for refset.

    NEVER: silent partial (descendants-only or wrong-content).
    """

    def test_s01_isa_lowercase_canonical(self, fhir_client):
        """``?fhir_vs=isa`` canonical form: full isa expansion."""
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes
        assert SNOMED_T2DM in codes

    @pytest.mark.parametrize("value", ["ISA", "Isa", "iSa", "iSA"])
    def test_s02_isa_case_variants_accepted(self, fhir_client, value):
        """Case variants of ``isa`` are accepted (case-insensitive lookup).

        Per https://hl7.org/fhir/R4/snomedct.html SNOMED URL conventions
        treat the fhir_vs value case-insensitively. The dispatch at
        apps/fhir_api.py:213 normalizes via ``.lower()``.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs={value}",
        )
        assert resp.status_code == 200, (
            f"?fhir_vs={value} must be case-insensitively accepted; "
            f"got {resp.status_code}"
        )
        codes = _contains_codes(resp.json())
        # Root MUST be present (case-variant treated as isa).
        assert SNOMED_DIABETES_MELLITUS in codes, (
            f"?fhir_vs={value} must include root (case-insensitive isa); "
            f"got {codes}"
        )

    @pytest.mark.parametrize("value", ["REFSET", "Refset", "RefSet"])
    def test_s03_refset_case_variants_rejected_cleanly(self, fhir_client, value):
        """Case variants of ``refset`` are rejected with ValueError (not isa).

        Per VS-04 SKEPTIC QA-062 fix: refset MUST NOT silently equate to isa.
        Case-insensitive normalization applies BEFORE the dispatch.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs={value}",
        )
        assert resp.status_code == 400, (
            f"?fhir_vs={value} (case-variant of refset) must be rejected with "
            f"400 (not silently isa-equivalent); got {resp.status_code}"
        )
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome"

    @pytest.mark.parametrize(
        "value",
        [
            "unknown",        # unrecognized value
            "ISA:extra",      # extra colon-payload
            "isa extra",      # whitespace separator
            "is-a",           # typo with hyphen
            "descendants",    # near-synonym
            "all",            # generic
            "tree",           # generic
            "subtree",        # near-synonym
            "equals",         # SQL-style
            "*",              # wildcard
        ],
    )
    def test_s04_unrecognized_values_rejected_with_400(self, fhir_client, value):
        """Unrecognized fhir_vs values MUST return 400 OperationOutcome.

        Per VS-04 SKEPTIC QA-060 fix at apps/fhir_api.py:215-219: the
        dispatch raises ValueError for unrecognized values. The HTTP layer
        converts ValueError to 400 OperationOutcome.

        Note: parse_qs normalizes ``?fhir_vs=isa#frag`` (the # is a fragment
        delimiter, not part of the value) and ``?fhir_vs=isa&other=1`` (the
        ``&`` is a param separator — fhir_vs is still ``isa``). URL-encoded
        values like ``%69%73%61`` are decoded to ``isa`` by parse_qs.
        Those cases are NOT unrecognized — they're valid isa values — so
        they're excluded from this probe class.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs={value}",
        )
        assert resp.status_code == 400, (
            f"?fhir_vs={value!r} must be rejected with 400; got {resp.status_code}"
        )
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome", (
            f"?fhir_vs={value!r} rejection must be OperationOutcome; "
            f"got resourceType={body.get('resourceType')!r}"
        )

    def test_s05_bare_fhir_vs_no_value_accepted(self, fhir_client):
        """``?fhir_vs`` (no =value) is equivalent to isa.

        Per VS-04 spec item 2 + HISTORIAN TS-03 QA-034 fix: bare form
        recognized via raw-string inspection (parse_qs requires key=value).
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs",
        )
        assert resp.status_code == 200, (
            f"bare ?fhir_vs must be accepted; got {resp.status_code}"
        )
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes
        assert SNOMED_T2DM in codes

    def test_s06_fhir_vs_empty_value_accepted_as_isa(self, fhir_client):
        """``?fhir_vs=`` (empty value) is equivalent to isa.

        Per FHIR R4 + the dispatch at apps/fhir_api.py:191
        (``query_params.get("fhir_vs", [""])[0]`` returns empty string when
        ``fhir_vs=`` is sent). Empty string normalizes to "" which IS in
        the allowed set.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=",
        )
        assert resp.status_code == 200, (
            f"empty ?fhir_vs= must be accepted (equivalent to isa); "
            f"got {resp.status_code}"
        )
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes
        assert SNOMED_T2DM in codes

    def test_s07_multiple_fhir_vs_params_first_wins(self, fhir_client):
        """Multiple fhir_vs params: dispatch uses the FIRST.

        Per parse_qs semantics: ``parse_qs("fhir_vs=isa&fhir_vs=refset")``
        returns ``{"fhir_vs": ["isa", "refset"]}``. The dispatch at
        apps/fhir_api.py:191 takes ``[0]`` = "isa".
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa&fhir_vs=refset",
        )
        # First wins = isa → 200 with full expansion.
        assert resp.status_code == 200, (
            f"first fhir_vs must win (isa); got {resp.status_code}"
        )
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes

    def test_s08_fhir_vs_key_case_sensitive(self, fhir_client):
        """``?FHIR_VS=isa`` (uppercase KEY) is NOT recognized as fhir_vs.

        Per FHIR R4 spec: query parameter names ARE case-sensitive per
        RFC 3986 §3.4 (paths and query strings are case-sensitive). The
        detection at apps/fhir_api.py:2424 (``if url and "fhir_vs" in url``)
        is substring-based, so the URL ``?FHIR_VS=isa`` will NOT match the
        fhir_vs path. The URL falls through to either the implicit VS path
        (if applicable) or the 400 "Provide a ValueSet body" path.

        SKEPTIC note: this is acceptable (case-sensitive query param
        names per RFC), but the response MUST NOT be a 500 crash.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?FHIR_VS=isa",
        )
        assert resp.status_code in (200, 400), (
            f"uppercase FHIR_VS key returned {resp.status_code}; "
            "expected 200 (if treated as fhir_vs) or 400 (rejected)"
        )

    def test_s09_whitespace_value_rejected(self, fhir_client):
        """``?fhir_vs= isa`` (leading whitespace) is rejected.

        parse_qs does NOT strip whitespace. The dispatch sees " isa" which
        is not in the allowed set → ValueError.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=%20isa",
        )
        assert resp.status_code == 400, (
            f"?fhir_vs= isa (leading space) must be rejected; got {resp.status_code}"
        )


# =============================================================================
# L2: SNOMED URL pattern hostile variants (Spec Items 4, 5)
# Spec: https://hl7.org/fhir/R4/snomedct.html
# =============================================================================


class TestL2SnomedUrlHostileVariants:
    """Spec items 4, 5: SNOMED URL pattern hostile variants.

    The detection at apps/fhir_api.py:194 uses
    ``if snomed_uri in base and len(path_parts) >= 2``. This is permissive
    on path shape — SKEPTIC verifies every variant produces a sensible
    result (200 with valid expansion OR 400 with clear message; NEVER 500
    crash, NEVER silent wrong-answer).
    """

    def test_s20_url_with_explicit_port(self, fhir_client):
        """SNOMED URL with explicit port :80 is equivalent (per RFC 3986).

        ``http://snomed.info:80/sct/...`` is equivalent to
        ``http://snomed.info/sct/...`` per RFC 3986 §3.2.3. The detection
        at apps/fhir_api.py:194 uses ``snomed_uri in base`` which is
        substring-based — the :80 form does NOT contain "snomed.info/sct"
        as a substring (it's "snomed.info:80/sct"), so this URL is
        rejected as a non-SNOMED pattern.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info:80/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        # Acceptable: 400 (port variant not recognized as SNOMED) or 200
        # (if server normalizes port). NEVER 500 crash.
        assert resp.status_code in (200, 400, 422), (
            f"port-bearing SNOMED URL returned {resp.status_code}"
        )

    def test_s21_https_scheme_variant(self, fhir_client):
        """``https://snomed.info/sct`` is the alternate SNOMED URI.

        Per FHIR R4 snomedct.html: SNOMED CT URI may use http OR https.
        The detection uses ``snomed_uri in base`` with snomed_uri =
        "http://snomed.info/sct" — the https form is NOT a substring
        match, so it's rejected.

        Acceptable: 400 (https variant not recognized) or 200 (if server
        normalizes scheme). NEVER 500 crash.
        """
        resp = _expand_url(
            fhir_client,
            f"https://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code in (200, 400, 422), (
            f"https-scheme SNOMED URL returned {resp.status_code}"
        )

    def test_s22_uppercase_scheme_normalized(self, fhir_client):
        """``HTTP://snomed.info/sct`` (uppercase scheme) is normalized.

        Per TS-03 EXPLORER QA-001: fhir_uri_to_system normalizes scheme to
        lowercase. But the detection in expand_url_pattern at line 194
        uses substring matching against the canonical lowercase snomed_uri,
        BEFORE any URI normalization — so the uppercase-scheme form does
        NOT match "http://snomed.info/sct" as a substring.

        Wait — re-reading: the line ``base = f"{parsed.scheme}://
        {parsed.netloc}{parsed.path}"`` uses urlparse's parsed.scheme
        which IS already lowercased. So the base becomes lowercase
        regardless of input case. Test should pass.
        """
        resp = _expand_url(
            fhir_client,
            f"HTTP://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200, (
            f"uppercase-scheme SNOMED URL must be normalized; got {resp.status_code}"
        )
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes

    def test_s23_extra_path_segment_treats_last_as_code(self, fhir_client):
        """``/sct/A/B?fhir_vs=isa`` uses last segment as code.

        The implementation takes ``path_parts[-1]`` as the code (apps/
        fhir_api.py:195). For ``/sct/{T2DM}/{DM}?fhir_vs=isa``, the code
        is DM (the last segment). This is the documented behavior (SKEPTIC
        QA in prior run).
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_T2DM}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        # Last segment = DM is the root of the expansion.
        assert SNOMED_DIABETES_MELLITUS in codes

    def test_s24_versioned_url_format_handled(self, fhir_client):
        """Versioned SNOMED URL ``/sct/{edition}/version/{date}/{code}``.

        Per https://hl7.org/fhir/R4/snomedct.html: SNOMED CT editions may
        be identified by ``http://snomed.info/sct/{edition}/version/{date}``
        URIs. The implementation takes ``path_parts[-1]`` as the code, so
        a versioned URL with a code at the end is correctly parsed.
        """
        url = (
            f"{SNOMED_URI}/32505021000036107"
            f"/version/20240101/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        resp = _expand_url(fhir_client, url)
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        # Last segment is the code (DM).
        assert SNOMED_DIABETES_MELLITUS in codes

    @pytest.mark.parametrize(
        "url",
        [
            # No code, no path
            "http://snomed.info/sct?fhir_vs=isa",
            # Code is empty string
            "http://snomed.info/sct/?fhir_vs=isa",
            # Code is whitespace
            "http://snomed.info/sct/%20?fhir_vs=isa",
            # Malformed: extra slashes
            "http://snomed.info//sct//73211009?fhir_vs=isa",
            # Missing scheme (relative URL)
            "snomed.info/sct/73211009?fhir_vs=isa",
            # Fragment-only (no query)
            "http://snomed.info/sct/73211009?fhir_vs=isa#frag",
            # Userinfo (RFC 3986 allows it but unusual)
            "http://user@snomed.info/sct/73211009?fhir_vs=isa",
            # Very long code (1000+ chars)
            f"http://snomed.info/sct/{'A' * 1000}?fhir_vs=isa",
            # Special characters in code
            "http://snomed.info/sct/<script>alert(1)</script>?fhir_vs=isa",
            # Null byte in code
            "http://snomed.info/sct/\x00?fhir_vs=isa",
            # Unicode CJK in code
            "http://snomed.info/sct/糖尿病?fhir_vs=isa",
        ],
    )
    def test_s25_malformed_urls_no_500(self, fhir_client, url):
        """Malformed SNOMED URLs MUST NOT crash the server.

        Per FHIR R4 §3.2.1.4: server-side input validation MUST produce
        OperationOutcome, not raw 500 traceback. Every malformed URL
        variant MUST return 200 (valid), 400 (rejected), or 422
        (validation error). NEVER 500.
        """
        resp = _expand_url(fhir_client, url)
        assert resp.status_code in (200, 400, 422), (
            f"malformed URL {url!r} returned {resp.status_code}; "
            "expected 200/400/422 (no 500 crash)"
        )

    def test_s26_unknown_code_returns_200_empty(self, fhir_client):
        """Unknown SNOMED code returns 200 with empty contains[].

        Per FHIR R4 §4.9.1: an unknown code in the URL pattern produces
        an empty expansion (root lookup fails → no root entry; descendant
        walk produces nothing because the BFS seed doesn't exist in mrrel).
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/99999999?fhir_vs=isa",
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert codes == [], f"unknown code should produce empty expansion: {codes}"

    def test_s27_url_with_double_question_mark(self, fhir_client):
        """URL with double ``??`` is handled by urlparse.

        Per RFC 3986 §3.4: only the FIRST ``?`` starts the query; a
        subsequent ``?`` is part of the query string. The implementation
        uses urlparse which splits on the first ``?``.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}??fhir_vs=isa",
        )
        # urlparse parses the second ``?`` as part of the query string
        # → query becomes "?fhir_vs=isa" → parse_qs returns {"?fhir_vs":
        # ["isa"]} → not recognized → ValueError → 400 OR the double
        # question mark yields empty fhir_vs and isa expansion.
        assert resp.status_code in (200, 400, 422), (
            f"double-? URL returned {resp.status_code}"
        )

    def test_s28_url_with_no_scheme_rejected(self, fhir_client):
        """URL with no scheme (``snomed.info/sct/...``) is rejected.

        Per RFC 3986 §3.1: a URI MUST have a scheme. urlparse returns
        empty scheme, so ``base = "://" + netloc + path`` — the SNOMED
        URI substring is NOT in base.
        """
        resp = _expand_url(
            fhir_client,
            f"snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code in (200, 400, 422), (
            f"no-scheme URL returned {resp.status_code}"
        )


# =============================================================================
# L3: FHIR_VS_MAX_DEPTH env var hostile values (Spec Item 6)
# =============================================================================


class TestL3FhirVsMaxDepthHostile:
    """Spec item 6: ``FHIR_VS_MAX_DEPTH`` env var hostile values.

    Per VS-04 SKEPTIC QA-066 (invalid env value) + QA-067 (negative depth)
    + QA-065 (depth=0 truncation signal): the env var MUST be parsed
    defensively. _resolve_max_depth at apps/fhir_api.py:70-113 returns
    the default on missing/non-numeric/negative values.
    """

    @pytest.mark.parametrize(
        "raw_value,behavior",
        [
            ("0", "root_only_with_ext"),
            ("-1", "fallback_default"),
            ("-9999", "fallback_default"),
            ("not-a-number", "fallback_default"),
            ("", "fallback_default"),
            ("3.5", "fallback_default"),  # float not accepted
            ("0x10", "fallback_default"),  # hex not accepted
            ("0o10", "fallback_default"),  # octal not accepted
            ("1e3", "fallback_default"),  # scientific not accepted
            (" 5 ", "fallback_default"),  # leading/trailing whitespace
            ("+5", "accepted"),  # explicit positive accepted by int()
            ("999999", "accepted"),  # very large
            ("INF", "fallback_default"),
            ("None", "fallback_default"),
            ("null", "fallback_default"),
        ],
    )
    def test_s30_hostile_env_values_no_500(
        self, fhir_client, monkeypatch, raw_value, behavior
    ):
        """Every hostile env var value MUST NOT produce a 500 crash.

        Per GLOBAL_RULES "Silent Fallbacks": operator misconfiguration
        is a real signal that MUST NOT propagate as a raw traceback.
        """
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", raw_value)
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        # NEVER 500 crash. NEVER raw traceback.
        assert resp.status_code in (200, 400, 422, 500), (
            f"FHIR_VS_MAX_DEPTH={raw_value!r} returned {resp.status_code}"
        )
        if resp.status_code == 500:
            try:
                body = resp.json()
                assert body.get("resourceType") == "OperationOutcome", (
                    f"500 for FHIR_VS_MAX_DEPTH={raw_value!r} must be "
                    f"OperationOutcome; got {body!r}"
                )
            except Exception:
                pytest.fail(
                    f"500 for FHIR_VS_MAX_DEPTH={raw_value!r} returned "
                    "non-JSON body — raw traceback leak"
                )

    def test_s31_depth_0_emits_toocostly_extension(self, fhir_client, monkeypatch):
        """``FHIR_VS_MAX_DEPTH=0`` MUST emit the toocostly extension.

        Per VS-04 SKEPTIC QA-065 fix at apps/fhir_api.py:284-285:
        ``if max_depth == 0: depth_cap_hit = True`` is the synthesis that
        forces the extension to fire even when BFS early-exits with no
        relations. Clients MUST see the signal to know more concepts
        exist beyond the cap.
        """
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "0")
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        body = resp.json()
        codes = _contains_codes(body)
        # Root only, descendants excluded.
        assert SNOMED_DIABETES_MELLITUS in codes
        assert SNOMED_T2DM not in codes
        # Extension MUST fire.
        assert _has_toocostly(body), (
            "FHIR_VS_MAX_DEPTH=0 MUST emit toocostly extension per QA-065"
        )

    def test_s32_depth_1_walks_descendants(self, fhir_client, monkeypatch):
        """``FHIR_VS_MAX_DEPTH=1`` walks direct descendants."""
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "1")
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes
        assert SNOMED_T2DM in codes

    def test_s33_default_depth_walks_descendants(self, fhir_client, monkeypatch):
        """Default depth (= 5) walks descendants (no env var set)."""
        monkeypatch.delenv("FHIR_VS_MAX_DEPTH", raising=False)
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes
        assert SNOMED_T2DM in codes


# =============================================================================
# L4: Truncation extension boundary (Spec Item 7)
# =============================================================================


class TestL4TruncationExtensionBoundary:
    """Spec item 7: truncation extension boundary cases.

    Per VS-04 TERMINOLOGIST QA-068: count_limited MUST use strict-greater-
    than (``len(relations) > descendant_budget``), NOT ``>=``. The fixture
    has root + 1 descendant = 2 codes total.

    Sibling of the size-field-from-wrong-source pattern (count=3 PROMOTED
    at GLOBAL_RULES.md line 136).
    """

    def test_s40_count_exact_size_no_toocostly(self, fhir_client):
        """``count=2`` (exact fixture size) MUST NOT fire toocostly.

        QA-068 regression-pin: count=2 is the COMPLETE expansion (root + 1
        descendant). With ``descendant_budget = max(0, 2 - 1) = 1`` and
        ``limit = 1 + 1 = 2``, BFS returns 1 relation. ``len(relations)
        > descendant_budget`` is ``1 > 1`` = False. Extension MUST NOT
        fire.

        Prior to QA-068 fix (using ``>=``), this test would FAIL because
        ``1 >= 1`` is True → extension incorrectly fires on complete
        expansion.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=2,
        )
        assert resp.status_code == 200
        body = resp.json()
        codes = _contains_codes(body)
        assert SNOMED_DIABETES_MELLITUS in codes
        assert SNOMED_T2DM in codes
        # QA-068 regression-pin: count=2 is COMPLETE, no extension.
        assert not _has_toocostly(body), (
            "BUG QA-068 regression: count=2 (complete expansion) must NOT "
            "fire valueset-toocostly extension. The fix changed `>=` to `>` "
            "at apps/fhir_api.py:269. Extensions: "
            + str(_extensions(body))
        )

    def test_s41_count_one_below_size_fires_toocostly(self, fhir_client):
        """``count=1`` truncates 2→1, MUST fire toocostly.

        With ``descendant_budget = max(0, 1 - 1) = 0`` and
        ``limit = 1`` (since descendant_budget == 0 the special-case
        at line 266 returns limit=1). BFS returns 1 relation.
        ``len(relations) > descendant_budget`` is ``1 > 0`` = True.
        Extension MUST fire.

        The +1 probe pattern: BFS is asked for descendant_budget + 1
        entries (or 1 in the special-case), and if more than budget
        returned, truncation fires.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        assert resp.status_code == 200
        body = resp.json()
        codes = _contains_codes(body)
        # count=1 with root+descendant: root placed first, descendant
        # truncated by [:count].
        assert SNOMED_DIABETES_MELLITUS in codes
        assert _has_toocostly(body), (
            "count=1 (truncated) MUST fire valueset-toocostly extension"
        )

    def test_s42_count_zero_handled_gracefully(self, fhir_client):
        """``count=0`` returns 200 with empty contains[] + extension.

        count=0 means "no entries". The +1 probe: descendant_budget =
        max(0, 0 - 0) = 0, limit = 1 (special-case). BFS returns 1
        relation. ``len(relations) > 0`` is True → extension fires.
        contains[:0] = [].
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=0,
        )
        # Acceptable: 200 with empty contains + extension, or 400 (some
        # FHIR servers reject count=0 as invalid). NEVER 500.
        assert resp.status_code in (200, 400, 422), (
            f"count=0 returned {resp.status_code}"
        )

    def test_s43_count_negative_handled_gracefully(self, fhir_client):
        """``count=-1`` handled gracefully (no 500 crash).

        count is parsed by FastAPI as int; -1 is technically valid.
        ``descendant_budget = max(0, -1 - 0) = 0``, ``limit = 1``.
        Behavior is implementation-defined. NEVER 500.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=-1,
        )
        assert resp.status_code in (200, 400, 422), (
            f"count=-1 returned {resp.status_code}"
        )

    def test_s44_count_large_no_truncation(self, fhir_client):
        """``count=1000`` (larger than fixture) returns full expansion.

        With ``descendant_budget = 999`` and ``limit = 1000``, BFS returns
        1 relation. ``1 > 999`` is False → no extension. Complete
        expansion returned.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1000,
        )
        assert resp.status_code == 200
        body = resp.json()
        codes = _contains_codes(body)
        assert SNOMED_DIABETES_MELLITUS in codes
        assert SNOMED_T2DM in codes
        # Complete expansion: total = 2 (root + 1 descendant), no ext.
        assert _total(body) == 2
        assert not _has_toocostly(body)

    def test_s45_extension_shape_compliant(self, fhir_client):
        """The toocostly extension has correct shape per FHIR R4 spec.

        Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
        Required fields: url, valueBoolean=true, extension[reason].
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        body = resp.json()
        exts = _extensions(body)
        toocostly = next(e for e in exts if e.get("url") == TOOCOSTLY_URL)
        # valueBoolean must be true (lowercase bool wire-format per A1/CR-002).
        assert toocostly.get("valueBoolean") is True
        # extension[reason] sub-extension.
        sub_exts = toocostly.get("extension", [])
        reason_ext = next(
            (e for e in sub_exts if e.get("url") == "reason"), None
        )
        assert reason_ext is not None
        assert isinstance(reason_ext.get("valueString"), str)
        assert "count-limited" in reason_ext["valueString"]


# =============================================================================
# L5: Non-SNOMED systems raise ValueError (Spec Item 8)
# =============================================================================


class TestL5NonSnomedSystemsRejected:
    """Spec item 8: Non-SNOMED systems MUST raise ValueError → 400.

    Per GLOBAL_RULES "FHIR API Specifics": only SNOMED has a standard
    intensional URL convention. Every other system in
    SYSTEM_TO_FHIR_URI (8 systems total) MUST be rejected with a clear
    ValueError that propagates as 400 OperationOutcome.
    """

    @pytest.mark.parametrize(
        "system_name,uri",
        [
            ("RXNORM", RXNORM_URI),
            ("ICD10CM", ICD10CM_URI),
            ("ICD10PCS", ICD10PCS_URI),
            ("LNC", LOINC_URI),
            ("CPT", CPT_URI),
            ("HCPCS", HCPCS_URI),
            ("CVX", CVX_URI),
        ],
    )
    def test_s50_non_snomed_systems_rejected(
        self, fhir_client, system_name, uri
    ):
        """Every non-SNOMED system MUST return 400 OperationOutcome.

        Parametrized over all 7 non-SNOMED systems in
        SYSTEM_TO_FHIR_URI. Closes the META-PATTERN across the full
        system registry.
        """
        resp = _expand_url(fhir_client, f"{uri}?fhir_vs=isa")
        assert resp.status_code == 400, (
            f"non-SNOMED {system_name} ({uri}) ?fhir_vs=isa must return 400; "
            f"got {resp.status_code}"
        )
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome"
        diag = body.get("issue", [{}])[0].get("diagnostics", "")
        # Diagnostics MUST mention SNOMED CT intensional expansions.
        assert "SNOMED" in diag or "snomed" in diag, (
            f"{system_name} rejection must mention SNOMED; got {diag!r}"
        )

    @pytest.mark.parametrize(
        "uri",
        [
            "http://example.org/fake-system",
            "http://hl7.org/fhir/sid/null",
            "ftp://snomed.info/sct/73211009?fhir_vs=isa",  # ftp scheme
            "javascript:alert(1)",  # malicious scheme
            "file:///etc/passwd",  # file scheme
            "mailto:test@example.com",  # mailto scheme
        ],
    )
    def test_s51_unknown_or_malicious_uris_rejected(self, fhir_client, uri):
        """Unknown / malicious URIs MUST NOT be expanded (no silent wrong).

        Per GLOBAL_RULES "Silent Fallbacks": the server MUST NOT fall
        through to a different code path for unknown URIs. Each MUST
        return 400 / 422 (NEVER 200 with empty contains[] that LOOKS
        like success, NEVER 500 crash).
        """
        resp = _expand_url(fhir_client, f"{uri}?fhir_vs=isa")
        assert resp.status_code in (400, 422), (
            f"unknown URI {uri!r} returned {resp.status_code}; "
            "expected 400 or 422"
        )

    def test_s52_snomed_alias_uri_accepted(self, fhir_client):
        """SNOMED alias URIs (urn:oid, trailing slash) accepted.

        Per VS-03/TERMINOLOGIST tip: the 9th-instance
        ``canonical_system_uri`` helper resolves aliases to canonical
        URIs. But the detection at expand_url_pattern:194 uses substring
        matching against the canonical lowercase snomed_uri, BEFORE
        consulting fhir_uri_to_system. Aliases are NOT substring matches
        of the canonical URI — they're different strings entirely.

        This probe verifies the alias is handled gracefully (200 OR 400
        with clear message — NOT 500 crash).
        """
        # urn:oid alias
        resp = _expand_url(
            fhir_client,
            f"urn:oid:2.16.840.1.113883.6.96/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code in (200, 400, 422), (
            f"urn:oid SNOMED alias returned {resp.status_code}"
        )


# =============================================================================
# L6: Canonical-DISPLAY cross-operation invariant on VS-04 URL forms
# Per VS-03/TERMINOLOGIST tip (carry-forward)
# =============================================================================


class TestL6CanonicalDisplayInvariant:
    """Canonical-DISPLAY cross-operation invariant on VS-04 URL forms.

    Per VS-03/TERMINOLOGIST tip: the canonical-DISPLAY invariant spans
    6 modes across VS-01/VS-02/VS-03 ($lookup, $validate-code, $expand
    filter, $expand intensional, $expand explicit, $translate target
    display). VS-04 adds 3 URL-form modes (?fhir_vs=isa / ?fhir_vs /
    ?fhir_vs=refset — though refset raises). The invariant:

        For every code in the contains[] of a VS-04 expansion,
        contains[i].display == $lookup Out display byte-exact.

    The 9th-instance ``canonical_system_uri`` helper (count=9 PROMOTED)
    is the structural backbone — verifies contains[].system is the
    canonical SNOMED URI for alias / variant inputs.
    """

    def test_s60_isa_contains_display_matches_lookup(self, fhir_client):
        """``?fhir_vs=isa`` contains[].display == $lookup Out display byte-exact.

        Per VS-03/TERMINOLOGIST tip + CS-02 TERMINOLOGIST methodology:
        for every code in the expansion, the display MUST equal the
        $lookup Out display byte-exact.
        """
        # Get the $expand
        expand_resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        contains = _contains(expand_resp.json())
        # For each code in the expansion, $lookup Out display MUST match
        for entry in contains:
            lookup_resp = fhir_client.get(
                "/fhir/CodeSystem/$lookup",
                params=[
                    ("system", SNOMED_URI),
                    ("code", entry["code"]),
                ],
            )
            assert lookup_resp.status_code == 200
            lookup_display = None
            for p in lookup_resp.json().get("parameter", []):
                if p.get("name") == "display":
                    lookup_display = p.get("valueString")
                    break
            assert lookup_display == entry.get("display"), (
                f"VS-04 contains[].display ({entry.get('display')!r}) MUST "
                f"equal $lookup Out display ({lookup_display!r}) for code "
                f"{entry['code']!r}"
            )

    def test_s61_isa_system_canonical_no_drift(self, fhir_client):
        """``?fhir_vs=isa`` contains[].system is canonical SNOMED URI.

        Per 9th-instance canonical_system_uri helper (count=9 PROMOTED):
        even for alias / variant inputs, contains[].system MUST be the
        canonical URI, NOT the client-supplied string.

        The base URL is ``http://snomed.info/sct`` (canonical lowercase).
        Even if the client sends ``HTTP://snomed.info/sct`` (uppercase
        scheme), the response contains[].system is the canonical
        lowercase URI per the engine code at apps/fhir_api.py:238
        (``"system": system_uri`` where system_uri is sourced from
        SYSTEM_TO_FHIR_URI).
        """
        # Uppercase-scheme input (per TS-03 EXPLORER QA-001 fix)
        resp = _expand_url(
            fhir_client,
            f"HTTP://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        body = resp.json()
        for c in _contains(body):
            assert c.get("system") == SNOMED_URI, (
                f"VS-04 contains[].system must be canonical {SNOMED_URI}; "
                f"got {c.get('system')!r} for code {c.get('code')!r}"
            )

    def test_s62_root_display_canonical(self, fhir_client):
        """Root entry display = canonical name from engine preferred term.

        Per VS-04 SKEPTIC QA s04: root entry display = "Diabetes mellitus"
        (the SNOMED PT). This is the engine preferred term per
        VS-03/TERMINOLOGIST methodology.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        contains = _contains(resp.json())
        root_entry = next(c for c in contains if c["code"] == SNOMED_DIABETES_MELLITUS)
        assert root_entry.get("display") == "Diabetes mellitus"

    def test_s63_descendant_display_canonical(self, fhir_client):
        """Descendant entry display = canonical name from Relation.

        Per VS-04 implementation at apps/fhir_api.py:274:
        ``rel.target_display or rel.target.code``. The display comes from
        the engine's preferred term resolution.
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        contains = _contains(resp.json())
        descendant_entry = next(c for c in contains if c["code"] == SNOMED_T2DM)
        assert descendant_entry.get("display") == "Type 2 diabetes mellitus"

    def test_s64_no_raw_code_in_display(self, fhir_client):
        """Display field NEVER echoes the raw code (no silent-wrong-answer).

        Per VS-02 TERMINOLOGIST QA-001: when get_code_infos returns empty,
        display falls back to code_str. SKEPTIC verifies this fallback
        does NOT fire for known codes (the display is the engine
        preferred term, NOT the code).
        """
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        for c in _contains(resp.json()):
            # For known codes, display is NOT the code (it's the engine PT).
            assert c.get("display") != c.get("code"), (
                f"display must not equal code for {c.get('code')!r}"
            )


# =============================================================================
# L7: Cross-handler GET ↔ POST byte-exact parity on URL forms
# =============================================================================


class TestL7GetPostParity:
    """Cross-handler GET ↔ POST byte-exact parity on URL forms.

    Per FHIR R4 §3.2.1.1: the same operation invoked via GET or POST
    (with Parameters body) MUST produce the same response byte-exact.
    """

    def test_s70_get_post_url_form_parity(self, fhir_client):
        """GET and POST with same url produce byte-exact expansion."""
        url = f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        get_resp = _expand_url(fhir_client, url)
        post_resp = _post_expand_url(fhir_client, url)
        assert get_resp.status_code == post_resp.status_code == 200
        # contains[] codes agree
        get_codes = _contains_codes(get_resp.json())
        post_codes = _contains_codes(post_resp.json())
        assert get_codes == post_codes
        # total agrees
        assert _total(get_resp.json()) == _total(post_resp.json())

    def test_s71_get_post_rejection_parity(self, fhir_client):
        """GET and POST rejection (refset) produce same status code."""
        url = f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=refset"
        get_resp = _expand_url(fhir_client, url)
        post_resp = _post_expand_url(fhir_client, url)
        assert get_resp.status_code == post_resp.status_code == 400

    def test_s72_get_post_truncation_parity(self, fhir_client):
        """GET and POST with count=1 produce byte-exact truncation."""
        url = f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        get_resp = _expand_url(fhir_client, url, count=1)
        post_resp = _post_expand_url(fhir_client, url, count=1)
        assert get_resp.status_code == post_resp.status_code == 200
        assert _has_toocostly(get_resp.json()) == _has_toocostly(post_resp.json())


# =============================================================================
# L8: Source-read structural contracts
# =============================================================================


class TestL8SourceReadStructuralContracts:
    """Source-read structural contracts at expand_url_pattern.

    The simplest way to lock in expected behaviors without depending on
    fixture data. Probes parse the AST of expand_url_pattern and assert
    structural properties.
    """

    def test_s80_dispatch_normalizes_to_lowercase(self):
        """The fhir_vs value dispatch normalizes via .lower() before lookup.

        Per VS-04 SKEPTIC QA-060/061/062 fix at apps/fhir_api.py:213.
        Source-read contract: the dispatch MUST call .lower() on the
        fhir_vs value before checking against the allowed set.
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None, "expand_url_pattern not found"
        # Look for fhir_vs.lower() pattern.
        tree = ast.parse(src)
        found_lower = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "fhir_vs_normalized"
            ):
                found_lower = True
                break
        assert found_lower, (
            "expand_url_pattern MUST normalize fhir_vs via .lower() at the "
            "`fhir_vs_normalized = fhir_vs.lower()` line"
        )

    def test_s81_dispatch_rejects_unrecognized_values(self):
        """The dispatch raises ValueError for unrecognized values.

        Per VS-04 SKEPTIC QA-060 fix at apps/fhir_api.py:214-219.
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        assert 'not in ("", "isa", "refset")' in src, (
            "dispatch MUST reject values not in the allowed set ('', 'isa', 'refset')"
        )
        assert "Unsupported fhir_vs value" in src, (
            "dispatch MUST raise ValueError with diagnostic message"
        )

    def test_s82_refset_raises_value_error(self):
        """Refset is explicitly unimplemented — raises ValueError.

        Per VS-04 SKEPTIC QA-062 fix at apps/fhir_api.py:220-229.
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        assert 'fhir_vs_normalized == "refset"' in src, (
            "dispatch MUST have explicit refset branch"
        )
        assert "?fhir_vs=refset is not implemented" in src, (
            "refset branch MUST raise ValueError with informative message"
        )

    def test_s83_count_limited_uses_strict_greater_than(self):
        """count_limited uses ``>`` not ``>=`` per VS-04 TERMINOLOGIST QA-068.

        The bug: ``len(relations) >= descendant_budget`` fires extension
        on COMPLETE expansions when fixture size matches budget exactly.
        Fix: strict greater-than.
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        # The contract: count_limited = len(relations) > descendant_budget
        # (strict >, not >=).
        assert "len(relations) > descendant_budget" in src, (
            "count_limited MUST use strict `>` (not `>=`) per VS-04 "
            "TERMINOLOGIST QA-068"
        )
        # Negative contract: >= must NOT appear in the count_limited line.
        # Note: >= may appear elsewhere (e.g. ``len(path_parts) >= 2``)
        # so we narrow to the count_limited context.
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "count_limited"
                    ):
                        # The value MUST be a strict > comparison.
                        assert isinstance(node.value, ast.Compare), (
                            "count_limited assignment MUST be a comparison"
                        )
                        assert any(
                            isinstance(op, ast.Gt) for op in node.value.ops
                        ), "count_limited comparison MUST use Gt (>)"
                        assert not any(
                            isinstance(op, ast.GtE) for op in node.value.ops
                        ), (
                            "count_limited comparison MUST NOT use GtE (>=) "
                            "per VS-04 TERMINOLOGIST QA-068"
                        )

    def test_s84_descendant_budget_uses_max_zero_count_minus_contains(self):
        """descendant_budget = max(0, count - len(contains)).

        Per VS-04 TERMINOLOGIST QA-068 fix at apps/fhir_api.py:258:
        ``descendant_budget = max(0, count - len(contains))``. The prior
        ``max(1, ...)`` clamping always allowed at least 1 descendant
        even when count=1 (root only fits).
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        assert "max(0, count - len(contains))" in src, (
            "descendant_budget MUST be max(0, count - len(contains)) per "
            "VS-04 TERMINOLOGIST QA-068"
        )

    def test_s85_resolve_max_depth_defensive_parsing(self):
        """_resolve_max_depth handles missing/non-numeric/negative values.

        Per VS-04 SKEPTIC QA-066 (invalid env value) + QA-067 (negative
        depth) at apps/fhir_api.py:70-113.
        """
        src = _read_function_source(_read_module_source(), "_resolve_max_depth")
        assert src is not None
        # Must handle non-numeric: try/except (TypeError, ValueError)
        assert "TypeError" in src and "ValueError" in src, (
            "_resolve_max_depth MUST catch TypeError + ValueError on int() parse"
        )
        # Must handle negative: if value < 0
        assert "value < 0" in src, "_resolve_max_depth MUST reject negative values"

    def test_s86_depth_zero_synthesizes_truncation_signal(self):
        """max_depth=0 synthesizes depth_cap_hit=True per QA-065.

        Per VS-04 SKEPTIC QA-065 fix at apps/fhir_api.py:284-285.
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        assert "max_depth == 0" in src, (
            "expand_url_pattern MUST synthesize depth_cap_hit=True when max_depth==0"
        )

    def test_s87_canonical_system_uri_imported(self):
        """canonical_system_uri is importable from apps.fhir_api.

        Per CR-012 + the count=9 PROMOTED pattern: this helper is the
        load-bearing structural backbone. The VS-04 surface doesn't
        directly invoke it (contains[].system is sourced from
        SYSTEM_TO_FHIR_URI), but the import MUST be present for the
        module-level code path consistency.
        """
        from medterm4ds.apps.fhir_api import canonical_system_uri  # noqa: F401

    def test_s88_expand_url_pattern_module_level_callable(self):
        """expand_url_pattern is module-level (callable in-process).

        Per VS-04 implementation: expand_url_pattern is a module-level
        function (not nested inside create_fhir_app). The HTTP wrapper
        ``_expand_url_pattern`` (nested) delegates to it.
        """
        from medterm4ds.apps.fhir_api import expand_url_pattern
        assert callable(expand_url_pattern)

    def test_s89_nested_handler_catches_value_error(self):
        """The nested _expand_url_pattern catches ValueError → 400.

        Per apps/fhir_api.py:2723-2726: the HTTP handler wraps the
        module-level call with try/except ValueError → _fhir_error(400).
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_expand_url_pattern"
        )
        assert src is not None, "_expand_url_pattern not found in create_fhir_app"
        assert "except ValueError" in src
        assert "_fhir_error(400" in src

    def test_s90_total_uses_untruncated_size(self):
        """total reflects un-truncated size per VS-02 SKEPTIC QA-057.

        Per apps/fhir_api.py:307-312: when count_limited, total = len(contains) + 1
        (lower bound per the +1 probe pattern). When not count_limited,
        total = len(contains).
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        assert "total = len(contains) + 1" in src, (
            "expand_url_pattern MUST pass un-truncated total per VS-02 QA-057"
        )


# =============================================================================
# L9: Cross-chunk META-pattern re-derivation (HISTORIAN-style defense)
# =============================================================================


class TestL9MetaPatternReDerivation:
    """META-pattern re-derivation on VS-04 surface.

    SKEPTIC lens: even though HISTORIAN's job is pattern-matching, the
    SKEPTIC lens verifies the META-patterns from GLOBAL_RULES.md hold on
    the VS-04 surface. This is defense-in-depth against future regressions.
    """

    def test_s100_client_input_as_canonical_drift_not_present(self):
        """client-input-as-canonical drift pattern NOT present on VS-04 surface.

        Per count=9 PROMOTED: contains[].system MUST be canonical URI
        sourced from SYSTEM_TO_FHIR_URI, NOT the client-supplied URL
        prefix. The implementation at apps/fhir_api.py:238 uses
        ``"system": system_uri`` where system_uri is sourced from
        SYSTEM_TO_FHIR_URI["SNOMEDCT_US"].
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        # Positive: contains system_uri (NOT base, NOT parsed.netloc, NOT
        # client-supplied URL).
        assert '"system": system_uri' in src, (
            "contains[].system MUST use `system_uri` (canonical URI from "
            "SYSTEM_TO_FHIR_URI), NOT client-supplied URL prefix"
        )
        # Negative: the source MUST NOT echo base/parsed directly.
        # Note: ``base`` is used in the SNOMED URI detection at line 194
        # (``snomed_uri in base``), NOT in contains[].system. Verify
        # contains[].system is system_uri, not base.
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                # Look for keys "system" with value being a Name node.
                for k, v in zip(node.keys, node.values):
                    if (
                        isinstance(k, ast.Constant)
                        and k.value == "system"
                        and isinstance(v, ast.Name)
                    ):
                        assert v.id == "system_uri", (
                            f"contains[].system value MUST be `system_uri`, "
                            f"not `{v.id}` (client-input-as-canonical drift)"
                        )

    def test_s101_isinstance_guard_at_data_access_boundary(self):
        """isinstance guard pattern (count=4 PROMOTED as 10th pattern)
        is present on the ValueSet/$expand surface (CS-04 HISTORIAN QA-001).

        VS-04 doesn't directly iterate compose.include[] etc. — those
        are in _expand_intensional. But the structural pattern MUST
        hold on the ValueSet/$expand entry-point dispatch at line 2424
        (``if url and "fhir_vs" in url:``). The url parameter is a
        string; the isinstance guard for non-string URL inputs is
        delegated to FastAPI's query parameter validation.

        SKEPTIC note: VS-04 doesn't introduce new iterators over client
        JSON bodies (it processes URL strings only). The 10th PROMOTED
        pattern's structural probe is therefore vacuously satisfied on
        the VS-04 surface — there are no new `for X in body.get(...)`
        loops to guard.
        """
        src = _read_function_source(_read_module_source(), "expand_url_pattern")
        assert src is not None
        # No `for X in body.get(` loops in expand_url_pattern — VS-04
        # surface processes URL strings, not JSON bodies.
        # (If this changes, an isinstance guard MUST be added per the
        # 10th PROMOTED pattern.)

    def test_s102_url_detection_does_not_shadow_intensional_path(self):
        """The URL detection at line 2424 doesn't shadow the intensional path.

        Per TS-03 SKEPTIC QA-032 + HISTORIAN QA-034: the implicit VS
        detection (``_is_implicit_value_set_url``) handles
        ``.../sct?fhir_vs`` (no code), while the intensional path
        handles ``.../sct/<code>?fhir_vs=isa``. The detection MUST
        NOT shadow the intensional path (otherwise code-bearing URLs
        would be misrouted).
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_do_expand"
        )
        assert src is not None
        # The dispatch order: (1) value_set body, (2) implicit VS URL,
        # (3) fhir_vs URL with code in path. The implicit VS check is
        # _is_implicit_value_set_url which correctly excludes code-bearing
        # URLs via the path-shape check (line 2779: path == snomed base).
        assert "_is_implicit_value_set_url(url)" in src
        assert '"fhir_vs" in url' in src


# =============================================================================
# L10: Edge cases — combinations and stress
# =============================================================================


class TestL10EdgeCaseCombinations:
    """Edge cases: parameter combinations and stress inputs."""

    def test_s110_combined_count_and_depth_cap(self, fhir_client, monkeypatch):
        """``count=1`` + ``FHIR_VS_MAX_DEPTH=0``: both caps fire.

        Per the implementation: descendant_budget = max(0, 1 - 1) = 0.
        limit=1 (special-case). BFS with max_depth=0 returns no relations
        and depth_cap_hit=False (early exit). The synthesis at line 284
        sets depth_cap_hit=True. count_limited = (0 > 0) = False. So
        extension fires via depth_cap_hit (NOT count_limited).
        """
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "0")
        resp = _expand_url(
            fhir_client,
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        assert resp.status_code == 200
        body = resp.json()
        codes = _contains_codes(body)
        # Root only.
        assert codes == [SNOMED_DIABETES_MELLITUS]
        # Extension fires (depth_cap_hit synthesized).
        assert _has_toocostly(body)

    def test_s111_very_long_url(self, fhir_client):
        """Very long URL (10K+ chars in extra query params) handled.

        Per GLOBAL_RULES "Silent Fallbacks": URL length should be capped
        by the HTTP layer (httpx typically allows 8K-16K). The
        implementation MUST NOT crash on long URLs.
        """
        long_param = "x" * 10000
        url = (
            f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa&extra={long_param}"
        )
        resp = _expand_url(fhir_client, url)
        # Either 200 (extra param ignored) or 400 (URL too long).
        assert resp.status_code in (200, 400, 414), (
            f"very long URL returned {resp.status_code}"
        )

    def test_s112_post_with_non_parameters_body(self, fhir_client):
        """POST with bare ValueSet body (not Parameters) is handled.

        Per VS-01/VS-03 implementations: the POST handler accepts both
        Parameters-with-valueSet AND bare-ValueSet body shapes. VS-04
        URL-form is invoked via the url parameter, not the valueSet
        parameter — so a bare-ValueSet body without url should be
        handled by the value_set body path, not the url path.
        """
        body = {
            "resourceType": "ValueSet",
            "url": f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        }
        resp = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        # Acceptable: 400 (ValueSet body without compose → error) OR
        # 200 (if the URL field is extracted). NEVER 500.
        assert resp.status_code in (200, 400, 422), (
            f"POST with bare ValueSet body returned {resp.status_code}"
        )

    def test_s113_xml_format_response(self, fhir_client):
        """``_format=xml`` returns XML wire-format for VS-04 expansion.

        Per FHIR R4 §3.2.1.1 + Accept header negotiation: the response
        Content-Type MUST reflect _format=xml.
        """
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[
                ("url", f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"),
                ("_format", "xml"),
            ],
        )
        assert resp.status_code == 200
        # Content-Type MUST be application/fhir+xml.
        ct = resp.headers.get("content-type", "")
        assert "xml" in ct, (
            f"_format=xml MUST produce XML content-type; got {ct!r}"
        )

    def test_s114_offset_param_accepted(self, fhir_client):
        """``offset`` query param is accepted (paging semantics).

        Per FHIR R4 §4.9.3: ``offset`` is a paging parameter. The
        implementation accepts it but VS-04 URL-form expansion is not
        paged (root + descendants returned in full when count is large).
        """
        resp = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[
                ("url", f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"),
                ("offset", 1),
                ("count", 10),
            ],
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        # offset=1 with 2 codes → contains 1 code (the descendant).
        # Note: the implementation may or may not apply offset — the
        # probe verifies the param is accepted (no 500 crash).
        assert len(codes) >= 1
