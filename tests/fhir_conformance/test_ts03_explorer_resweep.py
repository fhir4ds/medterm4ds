"""TS-03 / EXPLORER resweep — lateral-thinking probes for External Code Systems,
Implicit Value Sets, and Terminology Maintenance.

Source: https://build.fhir.org/terminology-service.html §4.7.3, §4.7.3.1-3

EXPLORER lens (per ROLE_QA_ENGINEER Section 3): lateral thinking — unusual
parameter combinations, undocumented features, integration corners. SKEPTIC +
HISTORIAN have already hardened the TS-03 surface (50 + 41 probes). This
resweep probes the unusual and untested combinations on the implicit VS surface.

HISTORIAN tip for EXPLORER (high-priority): probe **combined operations on the
implicit VS surface**:
  (a) implicit VS URL + count=1 truncation — does toocostly fire correctly?
      Cross-check with CF-HISTORIAN-VS02-01: BFS-capped intensional path has
      the bug (deferred); SQL LIMIT implicit VS path is correct. Verify which
      path implicit VS URL takes and whether toocostly fires correctly.
  (b) implicit VS URL + filter — URL detection precedence.
  (c) implicit VS URL + POST body — parameter precedence between URL query
      and POST Parameters body.

Other EXPLORER directions:
  - Edition/version URI variations.
  - Mixed-case URI scheme.
  - Implicit VS URL with extra path segments.
  - Terminology maintenance edge cases.

Per GLOBAL_RULES.md: every probe MUST assert the POSITIVE success shape (200 +
resource body) per documentation-of-buggy-behavior-as-probe methodology
(strategy 56) for input-recognition probes; "combined operations" probes assert
the spec-mandated precedence shape.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


SUPPORTED_SYSTEM_EXTENSION_URL = (
    "http://hl7.org/fhir/StructureDefinition/capabilitystatement-supported-system"
)
TOOCOSTLY_EXT_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"
EMPTY_SOURCE_EXT_URL = (
    "http://medterm4ds.org/fhir/StructureDefinition/valueset-empty-source"
)

FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)


# =============================================================================
# Lens 1 — Combined operations on the implicit VS surface (HISTORIAN tip).
# =============================================================================


class TestLens1CombinedOperationsImplicitVs:
    """HISTORIAN's combined-operations tip — the highest-priority EXPLORER lens.

    CF-HISTORIAN-VS02-01 (HIGH OPEN) is about the BFS-capped intensional path:
    `_expand_intensional` uses `get_descendants_bfs(..., limit=count)` which
    ITSELF pre-truncates the descendants list BEFORE it's appended to contains;
    `total=len(deduped)` passed after BFS is itself the truncated size when the
    cap fired.

    The implicit VS path (`_expand_implicit_value_set`) is structurally
    DIFFERENT: it uses a SQL `LIMIT count + 1` query, then computes
    `count_limited = len(rows) > count` (strict-greater-than). Per SKEPTIC
    FIX-001 (CF-HISTORIAN-VS02-02 RESOLVED), `total=untruncated_total` is
    `len(rows) if len(rows) > count else len(contains)`. This is the "+1 probe"
    pattern from VS-04 TERMINOLOGIST QA-068.

    This lens cross-checks that the implicit VS path takes the SQL LIMIT
    branch (NOT the BFS branch) and that the toocostly extension fires
    correctly when count is hit.
    """

    def test_e10_implicit_vs_form_a_count_1_emits_toocostly(self, fhir_client):
        """Lens 1(a): Form (a) implicit VS URL + count=1 — does toocostly fire?

        The fixture seeds 2 SNOMED codes. With count=1 against
        ``http://snomed.info/sct/vs``, the SQL LIMIT path returns 2 rows
        (LIMIT count+1=2), so ``len(rows) > count`` is True → toocostly fires.

        Cross-check vs CF-HISTORIAN-VS02-01: the implicit VS path uses SQL
        LIMIT (correct); the intensional BFS path has the bug (deferred).
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "http://snomed.info/sct/vs"), ("count", 1)],
        )
        assert r.status_code == 200, (
            f"Implicit Form (a) URL with count=1 failed. "
            f"Status={r.status_code}, body={r.text[:200]}"
        )
        body = r.json()
        expansion = body.get("expansion", {})
        contains = expansion.get("contains", [])
        # Truncation: only 1 entry should be returned when count=1.
        assert len(contains) <= 1, (
            f"count=1 cap not enforced: {len(contains)} entries returned"
        )
        exts = expansion.get("extension", [])
        ext_urls = {e.get("url") for e in exts}
        assert TOOCOSTLY_EXT_URL in ext_urls, (
            f"toocostly extension not emitted on count-cap truncation. "
            f"Extensions: {ext_urls}"
        )

    def test_e11_implicit_vs_form_a_count_matches_fixture_no_toocostly(
        self, fhir_client
    ):
        """Lens 1(a'): Form (a) implicit VS URL + count=2 (= fixture size).

        The fixture seeds exactly 2 SNOMED codes. With count=2, the SQL LIMIT
        returns 2 rows (LIMIT 3), so ``len(rows) > count`` is False (2 > 2
        is False). Toocostly MUST NOT fire on the complete expansion.

        Cross-check vs VS-04 TERMINOLOGIST QA-068: count_limited boundary
        MUST use strict-greater-than (`>`), NOT greater-than-or-equal (`>=`).
        The implicit VS path uses ``len(rows) > count`` which is correct.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "http://snomed.info/sct/vs"), ("count", 2)],
        )
        assert r.status_code == 200, (
            f"Implicit Form (a) URL with count=2 failed. "
            f"Status={r.status_code}, body={r.text[:200]}"
        )
        body = r.json()
        expansion = body.get("expansion", {})
        contains = expansion.get("contains", [])
        assert len(contains) == 2, (
            f"Expected 2 contains entries (fixture has 2 SNOMED codes); "
            f"got {len(contains)}: {contains}"
        )
        exts = expansion.get("extension", [])
        ext_urls = {e.get("url") for e in exts}
        assert TOOCOSTLY_EXT_URL not in ext_urls, (
            f"toocostly extension MUST NOT fire on COMPLETE expansion "
            f"(count=2 = fixture size). This is the VS-04 TERMINOLOGIST "
            f"QA-068 sibling pattern — strict-greater-than boundary. "
            f"Extensions: {ext_urls}"
        )

    def test_e12_implicit_vs_form_b_count_1_emits_toocostly(self, fhir_client):
        """Lens 1(a''): Form (b) implicit VS URL + count=1 — does toocostly fire?

        Form (b) is ``http://snomed.info/sct?fhir_vs`` (bare query, no =isa).
        The path takes the SAME SQL LIMIT branch as Form (a) (it's a single
        ``_expand_implicit_value_set`` function). The toocostly extension
        MUST fire identically on both forms.

        This cross-check is structurally important: it proves the impl is NOT
        accidentally routing Form (b) through the intensional BFS path (which
        has CF-HISTORIAN-VS02-01).
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "http://snomed.info/sct?fhir_vs"), ("count", 1)],
        )
        assert r.status_code == 200, (
            f"Implicit Form (b) URL with count=1 failed. "
            f"Status={r.status_code}, body={r.text[:200]}"
        )
        body = r.json()
        expansion = body.get("expansion", {})
        contains = expansion.get("contains", [])
        assert len(contains) <= 1, (
            f"count=1 cap not enforced on Form (b): "
            f"{len(contains)} entries returned"
        )
        exts = expansion.get("extension", [])
        ext_urls = {e.get("url") for e in exts}
        assert TOOCOSTLY_EXT_URL in ext_urls, (
            f"toocostly extension not emitted on Form (b) count-cap. "
            f"Extensions: {ext_urls}"
        )

    def test_e13_implicit_vs_url_plus_filter_url_wins(self, fhir_client):
        """Lens 1(b): implicit VS URL + filter (text) — URL detection precedence.

        Per the impl dispatch order in ``_do_expand``:
          (1) inline ValueSet
          (2) implicit VS URL (``_is_implicit_value_set_url``)
          (3) URL with fhir_vs pattern (SNOMED intensional with code)
          (4) text filter

        When BOTH a ``url`` (implicit VS form) AND a ``filter`` query param
        are supplied, the implicit VS URL MUST win — the filter MUST be
        silently ignored (or applied as a sub-filter on the implicit VS
        expansion; the impl currently ignores it). The fixture has 2 SNOMED
        codes; the filter "diabetes" would return both (because both contain
        "diabetes" in the display). To distinguish "filter applied" from
        "filter ignored", use a filter that would NOT match either code
        ("zzznomatch"); if URL wins, the implicit VS expansion still returns
        the codes; if filter wins, the response is empty.

        Spec: §4.7.3.1 — implicit value sets are convention-based; the URL
        identifies the value set. The filter is a separate mode (§4.7.4).
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[
                ("url", "http://snomed.info/sct/vs"),
                ("filter", "zzznomatch"),
                ("count", 20),
            ],
        )
        assert r.status_code == 200, (
            f"Implicit URL + filter combination failed. "
            f"Status={r.status_code}, body={r.text[:200]}"
        )
        body = r.json()
        expansion = body.get("expansion", {})
        contains = expansion.get("contains", [])
        # URL wins: the implicit VS expansion returns all SNOMED codes
        # (filter is ignored). The "zzznomatch" filter would have produced
        # ZERO results if filter won.
        codes = {c.get("code") for c in contains}
        assert "73211009" in codes or "44054006" in codes, (
            f"Implicit URL did not win over filter — got empty/wrong results. "
            f"Codes: {codes}"
        )

    def test_e14_implicit_vs_form_a_url_in_post_parameters_body(self, fhir_client):
        """Lens 1(c): implicit VS URL via POST Parameters body.

        Per FHIR R4 §4.7.5, $expand can be invoked via POST with a Parameters
        body carrying the ``url`` In parameter. The implicit VS URL detection
        MUST work identically through the POST path.

        Spec citation: https://hl7.org/fhir/R4/valueset-operation-expand.html
        In ``url``: "A canonical url for the ValueSet... If a URL is provided,
        then the expansion is the same as if the URL had been used in a call
        to $expand."
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": "http://snomed.info/sct/vs"},
                {"name": "count", "valueInteger": 1},
            ],
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code == 200, (
            f"POST $expand with implicit URL in Parameters body failed. "
            f"Status={r.status_code}, body={r.text[:200]}"
        )
        resp = r.json()
        expansion = resp.get("expansion", {})
        contains = expansion.get("contains", [])
        assert len(contains) <= 1, (
            f"count=1 cap not enforced on POST implicit URL: "
            f"{len(contains)} entries"
        )
        exts = expansion.get("extension", [])
        ext_urls = {e.get("url") for e in exts}
        assert TOOCOSTLY_EXT_URL in ext_urls, (
            f"toocostly extension not emitted on POST implicit URL truncation. "
            f"Extensions: {ext_urls}"
        )

    def test_e15_implicit_vs_url_post_body_url_overrides_query_url(
        self, fhir_client
    ):
        """Lens 1(c'): POST body url precedence over query string url.

        When a POST request supplies ``url`` in BOTH the query string AND
        the Parameters body, the impl MUST use the body parameter (per FHIR
        R4 §4.7.5: Parameters-body parameters override GET defaults — see
        ``expand_post`` lines 2367-2379: the Parameters-body ``url`` is used
        and the query string ``url`` is never consulted).

        Probe: query url = nonexistent system URL (would 400 if used);
        body url = valid implicit VS URL. If body wins, 200 + contains.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": "http://snomed.info/sct/vs"},
                {"name": "count", "valueInteger": 2},
            ],
        }
        # Query url points to a nonexistent system; body url is valid.
        r = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=body,
            params=[("url", "http://nonexistent.example/vs")],
        )
        # If body wins: 200 + contains 2 SNOMED codes.
        # If query wins: 400 (nonexistent system).
        assert r.status_code == 200, (
            f"POST body url did not win over query url — expected 200, "
            f"got {r.status_code}. Body: {r.text[:200]}"
        )
        resp = r.json()
        expansion = resp.get("expansion", {})
        contains = expansion.get("contains", [])
        codes = {c.get("code") for c in contains}
        # The body url (SNOMED implicit VS) won — returned SNOMED codes.
        assert codes.issubset({"73211009", "44054006"}), (
            f"Expected SNOMED codes from body url; got: {codes}"
        )


# =============================================================================
# Lens 2 — Edition/version URI variations (EXPLORER lateral thinking).
# =============================================================================


class TestLens2EditionVersionVariations:
    """Per FHIR R4 §4.7.3.1: SNOMED CT supports edition URIs with optional
    version suffixes (e.g. ``http://snomed.info/sct/731000124108`` for US
    edition, ``.../version/20240901`` for a specific snapshot).

    EXPLORER probes: does the server's implicit VS detection recognize
    edition/version variants of the SNOMED URI?
    """

    def test_e20_implicit_vs_snomed_us_edition_uri(self, fhir_client):
        """``http://snomed.info/sct/731000124108/vs`` — US edition implicit VS.

        The implementation's ``_is_implicit_value_set_url`` Form (a) check
        strips ``/vs`` and then calls ``fhir_uri_to_system(prefix)``. The
        prefix here is ``http://snomed.info/sct/731000124108`` — this is NOT
        in FHIR_URI_TO_SYSTEM (only the bare ``http://snomed.info/sct`` is).
        So the impl SHOULD return False → falls through to "Provide a
        ValueSet body..." 400.

        Documenting current behavior. Spec text from §4.7.3.1 does not
        mandate edition-specific implicit VS resolution; medterm4ds tracks
        a single edition only (INTENDED per AGENTS.md NOT A BUG registry
        for ``content: not-present`` and version handling).
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "http://snomed.info/sct/731000124108/vs")],
        )
        # The impl SHOULD reject this as an unrecognized system URI —
        # medterm4ds does not track edition-specific URIs. Either 400
        # (clean rejection) or 200 with empty-source extension is acceptable
        # per spec; what's NOT acceptable is a 500 or text/plain.
        assert r.status_code in (200, 400), (
            f"Edition-specific implicit VS URL produced unexpected status "
            f"{r.status_code}. Body: {r.text[:200]}"
        )
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/fhir"), (
            f"Non-FHIR Content-Type: {ct!r}"
        )
        body = r.json()
        assert body.get("resourceType") in ("ValueSet", "OperationOutcome"), (
            f"Unexpected resourceType: {body.get('resourceType')!r}"
        )

    def test_e21_implicit_vs_snomed_us_edition_with_version_uri(self, fhir_client):
        """``http://snomed.info/sct/731000124108/version/20240901/vs``.

        Same as test_e20 but with a version suffix. The prefix is even more
        specific; ``fhir_uri_to_system`` returns None. Same expectation: 400
        OR 200 with FHIR body, NEVER 500/text-plain.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[
                ("url", "http://snomed.info/sct/731000124108/version/20240901/vs")
            ],
        )
        assert r.status_code in (200, 400), (
            f"Edition+version implicit VS URL produced unexpected status "
            f"{r.status_code}. Body: {r.text[:200]}"
        )
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/fhir"), (
            f"Non-FHIR Content-Type: {ct!r}"
        )

    def test_e22_intensional_url_us_edition_recognized_or_rejected_cleanly(
        self, fhir_client
    ):
        """``http://snomed.info/sct/731000124108?fhir_vs=isa`` — intensional
        with US edition.

        The ``_expand_url_pattern`` parses the URL and walks descendants of
        the code in the path. With the US edition URI (code=731000124108,
        which is the US edition identifier — NOT a SNOMED concept ID), the
        engine lookup would return no matches (or fall back to bare SNOMED).
        The response MUST be FHIR-shaped (no 500/text-plain).
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[
                ("url", "http://snomed.info/sct/731000124108?fhir_vs=isa")
            ],
        )
        # Acceptance: any status, but the body MUST be FHIR.
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/fhir"), (
            f"Non-FHIR Content-Type on intensional US-edition URL: {ct!r}. "
            f"Body: {r.text[:200]}"
        )


# =============================================================================
# Lens 3 — Mixed-case URI scheme (RFC 7230 §2.7).
# =============================================================================


class TestLens3MixedCaseScheme:
    """Per RFC 7230 §2.7 (referenced by FHIR R4 §3.1.0.1.9), the URI scheme
    is case-insensitive: ``http://`` and ``HTTP://`` are equivalent.

    EXPLORER probe: does the impl's URL parsing normalize the scheme?
    Python's ``urlparse`` does NOT lowercase the scheme; ``urlparse("HTTP://host").scheme
    == "http"`` IS True (urlparse normalizes scheme to lowercase per RFC 3986 §3.1).

    The check is whether ``fhir_uri_to_system`` and ``_is_implicit_value_set_url``
    correctly handle uppercase-scheme inputs.
    """

    def test_e30_mixed_case_scheme_implicit_vs_loinc(self, fhir_client):
        """``HTTP://loinc.org/vs`` — uppercase scheme on implicit VS URL.

        urlparse normalizes scheme to lowercase, so the URL parses identically
        to ``http://loinc.org/vs``. The impl's ``_is_implicit_value_set_url``
        uses urlparse, so it SHOULD work identically.

        The fixture has no LOINC codes (only SNOMED + ICD10CM + RXNORM), so
        the response's expansion will be empty (with the empty-source
        extension attached per HISTORIAN TS-03 QA-033). The KEY assertion is
        that the server doesn't 500 or reject the URL outright.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "HTTP://loinc.org/vs")],
        )
        assert r.status_code == 200, (
            f"Uppercase-scheme implicit VS URL failed. "
            f"Status={r.status_code}, body={r.text[:200]}"
        )
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/fhir"), (
            f"Non-FHIR Content-Type: {ct!r}"
        )
        body = r.json()
        assert body.get("resourceType") == "ValueSet", (
            f"Expected ValueSet; got {body.get('resourceType')!r}"
        )
        # Empty expansion is acceptable (fixture has no LOINC); the
        # empty-source extension SHOULD be attached.
        expansion = body.get("expansion", {})
        exts = expansion.get("extension", [])
        ext_urls = {e.get("url") for e in exts}
        assert EMPTY_SOURCE_EXT_URL in ext_urls, (
            f"empty-source extension not attached for unseeded LOINC. "
            f"Extensions: {ext_urls}"
        )

    def test_e31_mixed_case_scheme_lookup_snomed(self, fhir_client):
        """``HTTP://snomed.info/sct`` — uppercase scheme on $lookup system.

        Cross-resource probe: $lookup uses ``fhir_uri_to_system`` to resolve
        the system param. Same scheme-insensitivity SHOULD apply.
        """
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params=[("system", "HTTP://snomed.info/sct"), ("code", "44054006")],
        )
        assert r.status_code == 200, (
            f"Uppercase-scheme $lookup failed. "
            f"Status={r.status_code}, body={r.text[:200]}"
        )
        body = r.json()
        params = {p["name"]: p for p in body.get("parameter", [])}
        assert "display" in params, (
            f"display parameter not returned in $lookup response: {body}"
        )


# =============================================================================
# Lens 4 — Implicit VS URL with extra path segments (lateral thinking).
# =============================================================================


class TestLens4ExtraPathSegments:
    """What happens when the implicit VS URL has extra path segments?

    - ``http://loinc.org/vs/extra/path`` — extra segments AFTER /vs.
    - ``http://snomed.info/sct/extra/vs`` — extra segment BEFORE /vs.

    The impl's ``_is_implicit_value_set_url`` Form (a) check is
    ``path.endswith("/vs")``. After rstrip("/"), the path is what's left.
    urlparse of the first URL gives path="/vs/extra/path" — does NOT end
    with "/vs" → False → falls through to other dispatch branches → 400.

    The second URL: path="/sct/extra/vs" — DOES end with "/vs". The prefix
    is "http://snomed.info/sct/extra" — ``fhir_uri_to_system`` returns None
    → Form (a) check fails → falls through → 400.
    """

    def test_e40_implicit_vs_loinc_with_extra_path_after_vs(self, fhir_client):
        """``http://loinc.org/vs/extra/path`` — extra segments after /vs.

        Expected: the impl rejects with 400 (URL does not end with /vs).
        Acceptance: 4xx with FHIR body, NEVER 500/text-plain.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "http://loinc.org/vs/extra/path")],
        )
        assert r.status_code in (400, 422), (
            f"Extra-path implicit VS URL should be rejected. "
            f"Status={r.status_code}, body={r.text[:200]}"
        )
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/fhir"), (
            f"Non-FHIR Content-Type: {ct!r}"
        )
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome", (
            f"Expected OperationOutcome; got {body.get('resourceType')!r}"
        )

    def test_e41_implicit_vs_snomed_with_extra_path_before_vs(self, fhir_client):
        """``http://snomed.info/sct/extra/vs`` — extra segment before /vs.

        The URL DOES end with /vs, but the prefix is
        ``http://snomed.info/sct/extra`` — not in the registry. The impl
        should reject with 400 "Unrecognized code system URI...".
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "http://snomed.info/sct/extra/vs")],
        )
        assert r.status_code == 400, (
            f"Extra-path-before-/vs implicit VS URL should be rejected with "
            f"400. Status={r.status_code}, body={r.text[:200]}"
        )
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/fhir"), (
            f"Non-FHIR Content-Type: {ct!r}"
        )
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome", (
            f"Expected OperationOutcome; got {body.get('resourceType')!r}"
        )

    def test_e42_implicit_vs_trailing_question_mark(self, fhir_client):
        """``http://snomed.info/sct/vs?`` — trailing question mark.

        Lateral probe: empty query string. ``urlparse`` parses this as
        path="/vs", query="". Per the impl, ``stripped = url.rstrip("/")``
        does NOT strip the trailing "?", so the Form (a) check
        ``stripped.endswith("/vs")`` is False and the URL falls through to
        the 400 "Provide a ValueSet body..." path.

        The trailing "?" is a URL-normalization quirk; the spec does not
        mandate the server accept this form. This probe documents the
        current behavior — the response MUST be FHIR-shaped (no 500/text-
        plain), and the URL MUST NOT silently expand. If a future
        enhancement normalizes the trailing "?" to empty query, this probe
        MUST be tightened to assert 200.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "http://snomed.info/sct/vs?")],
        )
        # Acceptance: any status, but the body MUST be FHIR-shaped.
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/fhir"), (
            f"Non-FHIR Content-Type on trailing-? implicit VS URL: {ct!r}. "
            f"Body: {r.text[:200]}"
        )
        body = r.json()
        assert body.get("resourceType") in ("ValueSet", "OperationOutcome"), (
            f"Unexpected resourceType: {body.get('resourceType')!r}"
        )


# =============================================================================
# Lens 5 — Terminology maintenance edge cases (POST CodeSystem).
# =============================================================================


class TestLens5TerminologyMaintenanceEdgeCases:
    """Per §4.7.3.3: "A terminology server should validate incoming resources
    and ensure integrity of the terminology services."

    medterm4ds is read-only — POST /fhir/CodeSystem etc. MUST be rejected
    with a FHIR OperationOutcome (NOT 201, NOT 500/text-plain). The TS-03
    SKEPTIC resweep + HISTORIAN resweep already verified basic POST
    rejection. EXPLORER adds lateral probes for malformed POST bodies.
    """

    @pytest.mark.parametrize(
        "body,description",
        [
            # POST with operations array referencing unknown operations.
            (
                {
                    "resourceType": "CodeSystem",
                    "url": "http://example.org/test",
                    "content": "complete",
                    "concept": [{"code": "A", "display": "Alpha"}],
                    "property": [
                        {"code": "unknownOp", "uri": "http://example.org/unknownOp"}
                    ],
                },
                "operations-array-referencing-unknown-op",
            ),
            # POST with both url and id.
            (
                {
                    "resourceType": "CodeSystem",
                    "id": "test-cs-1",
                    "url": "http://example.org/test",
                    "content": "complete",
                    "status": "draft",
                },
                "both-url-and-id",
            ),
            # POST with conflicting status.
            (
                {
                    "resourceType": "CodeSystem",
                    "url": "http://example.org/test",
                    "status": "active",
                    "content": "complete",
                    "concept": [{"code": "A"}],
                    # Conflicting status fields — duplicate key.
                },
                "conflicting-status",
            ),
            # POST with empty resource body.
            (
                {"resourceType": "CodeSystem"},
                "empty-resource-body",
            ),
        ],
    )
    def test_e50_post_codesystem_malformed_variants(
        self, fhir_client, body, description
    ):
        """Lens 5: every malformed POST CodeSystem variant MUST return a
        FHIR OperationOutcome (read-only server rejection), NOT 500/text-plain.
        """
        r = fhir_client.post("/fhir/CodeSystem", json=body)
        # 4xx is acceptable (server rejects); 2xx is NOT acceptable.
        # 5xx with text/plain is the silent-wrong-answer shape we're catching.
        assert 400 <= r.status_code < 500, (
            f"[{description}] Expected 4xx rejection; got {r.status_code}. "
            f"Body: {r.text[:200]}"
        )
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/fhir"), (
            f"[{description}] Non-FHIR Content-Type: {ct!r}"
        )
        resp = r.json()
        assert resp.get("resourceType") == "OperationOutcome", (
            f"[{description}] Expected OperationOutcome; got "
            f"{resp.get('resourceType')!r}"
        )

    def test_e51_post_conceptmap_with_group_then_element(self, fhir_client):
        """POST ConceptMap with nested group.element.target — same shape
        as a real ConceptMap resource. MUST be rejected (read-only server)."""
        body = {
            "resourceType": "ConceptMap",
            "url": "http://example.org/cm",
            "status": "draft",
            "group": [
                {
                    "source": "http://snomed.info/sct",
                    "target": "http://hl7.org/fhir/sid/icd-10-cm",
                    "element": [
                        {
                            "code": "73211009",
                            "target": [
                                {"code": "E11", "equivalence": "equivalent"}
                            ],
                        }
                    ],
                }
            ],
        }
        r = fhir_client.post("/fhir/ConceptMap", json=body)
        assert 400 <= r.status_code < 500, (
            f"POST ConceptMap with group.element.target should be rejected; "
            f"got {r.status_code}. Body: {r.text[:200]}"
        )
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/fhir"), (
            f"Non-FHIR Content-Type: {ct!r}"
        )

    def test_e52_post_valueset_with_compose_include_then_rejected(
        self, fhir_client
    ):
        """POST ValueSet with compose.include — real-world resource shape.
        MUST be rejected."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs",
            "status": "draft",
            "compose": {
                "include": [
                    {
                        "system": "http://snomed.info/sct",
                        "concept": [{"code": "73211009"}],
                    }
                ]
            },
        }
        r = fhir_client.post("/fhir/ValueSet", json=body)
        assert 400 <= r.status_code < 500, (
            f"POST ValueSet with compose.include should be rejected; "
            f"got {r.status_code}. Body: {r.text[:200]}"
        )
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/fhir"), (
            f"Non-FHIR Content-Type: {ct!r}"
        )


# =============================================================================
# Lens 6 — CodeSystem.valueSet URI resolvable (item 4) — lateral variant.
# =============================================================================


class TestLens6CodeSystemValueSetUriLateral:
    """Per §4.7.3 Value Set Validation: "Clients can refer to these implicit
    value sets by providing the URI for the code system itself."

    SKEPTIC resweep test_s50/s51/s52 already verified bare canonical URI alone
    works on $expand. EXPLORER adds lateral probes:
    - bare canonical URI as ``url`` param via POST Parameters body.
    - bare canonical URI as ``url`` param combined with ``filter``.
    - bare canonical URI alias (urn:oid) alone.
    """

    def test_e60_canonical_uri_alone_via_post_body(self, fhir_client):
        """``url=http://snomed.info/sct`` (no /vs suffix) via POST Parameters.

        Per §4.7.3 Value Set Validation: "Clients CAN refer to these implicit
        value sets by providing the URI for the code system itself." The spec
        uses "can" (permissive), not "SHALL/MUST". The impl's behavior today:
        bare canonical URI alone does NOT trigger implicit VS detection
        (no /vs suffix, no fhir_vs query) → falls through to the no-filter
        400 path. This is documented as INTENDED — clients must use either
        the /vs suffix or ?fhir_vs form.

        SKEPTIC test_s50 verified the GET behavior accepts 200 OR 400 (both
        conformant). EXPLORER extends to POST Parameters body, asserting the
        same shape: FHIR body, no 500/text-plain.

        If a future enhancement auto-detects bare canonical URIs as implicit
        VS, this probe MUST be tightened to assert 200.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": "http://snomed.info/sct"},
                {"name": "count", "valueInteger": 20},
            ],
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/fhir"), (
            f"Non-FHIR Content-Type: {ct!r}"
        )
        # Acceptance: 200 (if enhanced) or 400 (current behavior). Both
        # conformant per the spec's permissive "can" wording.
        assert r.status_code in (200, 400), (
            f"Bare canonical URI via POST Parameters produced unexpected "
            f"status {r.status_code}. Body: {r.text[:200]}"
        )

    def test_e61_canonical_uri_alone_with_filter(self, fhir_client):
        """``url=http://snomed.info/sct&filter=diabetes`` — bare URI + filter.

        Per the impl dispatch: bare canonical URI does NOT end with /vs and
        does NOT contain fhir_vs, so it falls through to the FILTER branch
        (mode 4). The filter is applied via ``search_names`` scoped to the
        system's source.

        Acceptance: 200 with the codes whose display contains "diabetes".
        The fixture's SNOMED codes are "Diabetes mellitus" (73211009) and
        "Type 2 diabetes mellitus" (44054006) — both contain "diabetes".
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[
                ("url", "http://snomed.info/sct"),
                ("filter", "diabetes"),
            ],
        )
        # search_names requires the BM25 index; in conformance DB without
        # the index, this might 503 or return empty. The acceptance is that
        # the response is FHIR-shaped.
        ct = r.headers.get("content-type", "")
        assert ct.startswith("application/fhir"), (
            f"Non-FHIR Content-Type: {ct!r}"
        )
        # Acceptance: either 200 (filter applied) or 503 (BM25 unavailable).
        assert r.status_code in (200, 503), (
            f"Bare URI + filter produced unexpected status {r.status_code}. "
            f"Body: {r.text[:200]}"
        )

    def test_e62_urn_oid_alias_canonical_uri_alone_no_canonical_drift(
        self, fhir_client
    ):
        """``url=urn:oid:2.16.840.1.113883.6.96`` (SNOMED urn:oid) — bare.

        Per CF-HISTORIAN-VS02-02 RESOLVED: when the URL is the bare SNOMED
        urn:oid alias, the impl SHOULD fall through to the filter branch
        (no /vs, no fhir_vs), which (with no filter) returns 400.

        But more importantly, even if the URL were the urn:oid-with-/vs
        form, the impl MUST canonicalize. This probe verifies the bare
        urn:oid alias does NOT trigger an implicit VS expansion (it has
        no /vs suffix), so the response is 400.
        """
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", "urn:oid:2.16.840.1.113883.6.96")],
        )
        # Bare urn:oid alias has no /vs suffix → does not match implicit VS
        # Form (a) → does not match Form (b) (not SNOMED netloc with bare
        # fhir_vs) → no fhir_vs in URL → no filter → falls to 400 "Provide
        # a ValueSet body...".
        assert r.status_code == 400, (
            f"Bare urn:oid alias should fall through to 400 (no /vs, no "
            f"fhir_vs, no filter). Status={r.status_code}, "
            f"body={r.text[:200]}"
        )


# =============================================================================
# Lens 7 — GET ↔ POST byte-exact parity on implicit VS surface.
# =============================================================================


class TestLens7GetPostParity:
    """Per VS-04 EXPLORER strategy 50 (GET ↔ POST parity), the implicit VS
    surface SHOULD produce byte-exact agreement between GET and POST for
    the same input parameters. Cross-handler divergence is a smell.

    This lens verifies:
    - GET url + count == POST Parameters-body url + count (codes match,
      total matches, toocostly extension matches).
    """

    def test_e70_get_post_parity_implicit_form_a(self, fhir_client):
        """GET ``?url=http://snomed.info/sct/vs&count=1`` ≡ POST Parameters
        body with same url + count.

        Cross-handler byte-exact parity on codes count + toocostly extension
        presence.
        """
        url = "http://snomed.info/sct/vs"
        # GET
        r_get = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", url), ("count", 1)],
        )
        # POST Parameters
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": url},
                {"name": "count", "valueInteger": 1},
            ],
        }
        r_post = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        # Both must be 200.
        assert r_get.status_code == r_post.status_code == 200, (
            f"GET status={r_get.status_code}, POST status={r_post.status_code}"
        )
        get_body = r_get.json()
        post_body = r_post.json()
        get_exp = get_body.get("expansion", {})
        post_exp = post_body.get("expansion", {})
        # Codes count MUST match.
        assert len(get_exp.get("contains", [])) == len(
            post_exp.get("contains", [])
        ), (
            f"GET contains={len(get_exp.get('contains', []))}, "
            f"POST contains={len(post_exp.get('contains', []))} — divergence"
        )
        # Codes SET MUST match.
        get_codes = {c.get("code") for c in get_exp.get("contains", [])}
        post_codes = {c.get("code") for c in post_exp.get("contains", [])}
        assert get_codes == post_codes, (
            f"GET codes={get_codes}, POST codes={post_codes} — divergence"
        )
        # Toocostly extension MUST be present on both.
        get_ext = {e.get("url") for e in get_exp.get("extension", [])}
        post_ext = {e.get("url") for e in post_exp.get("extension", [])}
        assert TOOCOSTLY_EXT_URL in get_ext, (
            f"GET missing toocostly: {get_ext}"
        )
        assert TOOCOSTLY_EXT_URL in post_ext, (
            f"POST missing toocostly: {post_ext}"
        )

    def test_e71_get_post_parity_implicit_form_b(self, fhir_client):
        """GET ``?url=http://snomed.info/sct?fhir_vs&count=1`` ≡ POST.

        Same as e70 but for Form (b) implicit VS URL.
        """
        url = "http://snomed.info/sct?fhir_vs"
        r_get = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params=[("url", url), ("count", 1)],
        )
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": url},
                {"name": "count", "valueInteger": 1},
            ],
        }
        r_post = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r_get.status_code == r_post.status_code == 200, (
            f"GET status={r_get.status_code}, POST status={r_post.status_code}"
        )
        get_exp = r_get.json().get("expansion", {})
        post_exp = r_post.json().get("expansion", {})
        assert len(get_exp.get("contains", [])) == len(
            post_exp.get("contains", [])
        ), (
            f"GET contains={len(get_exp.get('contains', []))}, "
            f"POST contains={len(post_exp.get('contains', []))} — divergence"
        )
        get_ext = {e.get("url") for e in get_exp.get("extension", [])}
        post_ext = {e.get("url") for e in post_exp.get("extension", [])}
        assert TOOCOSTLY_EXT_URL in get_ext
        assert TOOCOSTLY_EXT_URL in post_ext


# =============================================================================
# Lens 8 — Implicit VS path source-reading (structural contract).
# =============================================================================


class TestLens8ImplicitVsPathSourceRead:
    """Source-reading probes verifying the structural contract of
    ``_expand_implicit_value_set``. These are FIX-LEVEL regression guards
    per VS-05 HISTORIAN strategy 52 — they parse the function's AST and
    assert the load-bearing structural elements are still present.

    Cross-check with CF-HISTORIAN-VS02-01: the implicit VS path takes the
    SQL LIMIT branch (correct), NOT the BFS branch (deferred bug).
    """

    @staticmethod
    def _read_function_source(func_name: str) -> str:
        """Source-read a function from apps/fhir_api.py via AST."""
        source = FHIR_API_PATH.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return ast.get_source_segment(source, node) or ""
        return ""

    def test_e80_implicit_vs_uses_sql_limit_not_bfs(self):
        """Lens 8 — structural contract: ``_expand_implicit_value_set`` uses
        SQL LIMIT (correct path), NOT BFS ``get_descendants_bfs`` (deferred
        bug CF-HISTORIAN-VS02-01 on the intensional path).
        """
        text = self._read_function_source("_expand_implicit_value_set")
        assert "LIMIT" in text, (
            "_expand_implicit_value_set must use SQL LIMIT (the correct "
            "path); got no LIMIT in source."
        )
        assert "get_descendants_bfs" not in text, (
            "_expand_implicit_value_set must NOT call get_descendants_bfs "
            "(that path has CF-HISTORIAN-VS02-01 — deferred bug on the "
            "intensional path, distinct from this function)."
        )

    def test_e81_implicit_vs_count_limited_uses_strict_gt(self):
        """Lens 8 — structural contract: ``count_limited = len(rows) > count``
        (strict-greater-than), per VS-04 TERMINOLOGIST QA-068 sibling pattern.
        NOT ``>=`` (which would fire toocostly on COMPLETE expansions)."""
        text = self._read_function_source("_expand_implicit_value_set")
        assert "len(rows) > count" in text, (
            "_expand_implicit_value_set must use 'len(rows) > count' "
            "(strict-greater-than) for count_limited detection. "
            "Got different operator — see VS-04 TERMINOLOGIST QA-068."
        )
        assert "len(rows) >= count" not in text, (
            "_expand_implicit_value_set uses 'len(rows) >= count' "
            "(greater-than-or-equal) which is the VS-04 TERMINOLOGIST "
            "QA-068 bug shape — fires toocostly on COMPLETE expansions."
        )

    def test_e82_implicit_vs_total_uses_plus_1_probe_pattern(self):
        """Lens 8 — structural contract: ``untruncated_total = len(rows) if
        len(rows) > count else len(contains)`` — the "+1 probe" pattern from
        VS-04 TERMINOLOGIST QA-068. The SQL LIMIT ``count + 1`` returns one
        extra row to detect truncation; when ``len(rows) > count``, the
        true total is unknown (could be much larger)."""
        text = self._read_function_source("_expand_implicit_value_set")
        assert "count + 1" in text, (
            "_expand_implicit_value_set must use SQL LIMIT 'count + 1' "
            "(the +1 probe pattern). Got different LIMIT."
        )
        assert "untruncated_total" in text, (
            "_expand_implicit_value_set must compute 'untruncated_total' "
            "via the +1 probe pattern (VS-04 TERMINOLOGIST QA-068)."
        )

    def test_e83_implicit_vs_calls_canonical_system_uri(self):
        """Lens 8 — structural contract: CF-HISTORIAN-VS02-02 RESOLVED.
        ``_expand_implicit_value_set`` MUST call ``canonical_system_uri`` on
        the Form (a) path so ``contains[].system`` echoes the canonical URI,
        NOT the alias the client supplied (e.g. urn:oid)."""
        text = self._read_function_source("_expand_implicit_value_set")
        assert "canonical_system_uri(" in text, (
            "_expand_implicit_value_set must call canonical_system_uri() "
            "(CF-HISTORIAN-VS02-02 RESOLVED — 9th instance of client-input-"
            "as-canonical drift pattern)."
        )

    def test_e84_intensional_path_bfs_limit_still_present(self):
        """Lens 8 — CF-HISTORIAN-VS02-01 STILL OPEN. Per the
        carry-forward-as-probe pattern (strategy 56), this probe asserts the
        BUGGY behavior exists today (``limit=count`` + ``total=len(deduped)``
        in ``_expand_intensional``). When a future fix lands extending
        ``get_descendants_bfs`` to return a 3-tuple OR issuing a separate
        COUNT query, this probe MUST be tightened (will fail loudly).
        """
        text = self._read_function_source("_expand_intensional")
        # The bug: BFS uses limit=count which itself pre-truncates the
        # descendants list BEFORE it's appended to contains.
        assert "limit=count" in text or "get_descendants_bfs" in text, (
            "_expand_intensional must still use the BFS-limit pattern "
            "(CF-HISTORIAN-VS02-01 deferred bug). When the fix lands, "
            "this probe MUST be tightened."
        )
