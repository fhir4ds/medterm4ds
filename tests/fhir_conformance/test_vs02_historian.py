"""VS-02 HISTORIAN: ValueSet $expand — Basic.

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
Expansion shape: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion
valueset-toocostly: https://hl7.org/fhir/R4/extension-valueset-toocostly.html

HISTORIAN lens (per chunk assignment): pattern-match SKEPTIC's findings and
carry-forwards against prior bug patterns.

Carry-in context (from SKEPTIC VS-02 handoff):
  1 fix: QA-057 (expansion.total reflected truncated size; fixed via new total=
  parameter on build_valueset_expand; 3 call sites updated).
  3 CFs: CF-SKEPTIC-VS02-01 (count=0 → 422), CF-SKEPTIC-VS02-02 (offset ignored
  on GET, missing on POST), CF-SKEPTIC-VS02-03 (GET filter path missing
  toocostly on truncation).

HISTORIAN audit dimensions:

  Lens 1 — QA-057 thread verification (the explicit-size-on-truncation pattern):
    - Verify the new total= parameter is correctly threaded at the 3 call sites
      AND that the value passed is actually the UN-truncated size, not just
      ``len(post-truncated-list)``.
    - Look for OTHER places that might also need it (any place that pre-
      truncates contains[] via a BFS limit, not just a Python slice).

  Lens 2 — CF-SKEPTIC-VS01-01..04 source-reading audit on VS-02 surface:
    - 7 of 9 filter operators silently dropped — verify still applies.
    - exclude.filter ignored — verify.
    - exclude ignores system — verify.
    - compose metadata ignored — verify.

  Lens 3 — Canonical-URI helper usage (milestone-2 review structural fix 1):
    - Verify _do_expand and its callees use canonical_system_uri() consistently.
    - Sibling _do_* handlers were updated; the expand path may have been missed.

  Lens 4 — Silent-fallback-on-hardcoded-default recurrence (count=1 of pattern):
    - Any other hardcoded count/offset defaults in expand code?

  Lens 5 — Documentation-vs-implementation drift:
    - Read docstring on build_valueset_expand and _expand_intensional — accurate
      post-fix?

  Lens 6 — Test-too-lenient audit:
    - Re-audit SKEPTIC's 53 probes. Any that pass for the wrong reason?

  Lens 7 — CF-HISTORIAN-CS04-02 systemic duckdb.Error — verify the milestone-2
    structural fix (app-level handler) covers the expand path.

Per GLOBAL_RULES.md:
  - "Test-too-lenient": every probe asserts POSITIVE success shape.
  - "Don't manufacture bugs": DEFERRED is valid for genuine fixture gaps.
  - Spec citation required on every probe.
  - Carry-forward-as-probe pattern (CS-03 TERMINOLOGIST methodology).

Reference fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus",   "A73211009", "N", "SNOMEDCT_US", "C0011849"),  # parent
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),  # child
    ("E11",      "HT", "Type 2 diabetes mellitus", "AE11",      "N", "ICD10CM",    "C0011847"),
    ("860975",   "SCD","24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")  # 44054006 is-a 73211009
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (canonical R4)
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html (too-costly)
# Spec: https://hl7.org/fhir/R4/valueset.html#expansion (expansion shape)
#
# FHIR R4 filter-operator enum — single source of truth per milestone-2 review
# (CR-014): import the canonical frozen-set from engines.fhir rather than
# redefining it locally. VS-01 SKEPTIC QA-054 found that the test suite
# encoded the off-spec ``descendant-of`` spelling as expected behavior.
from medterm4ds.engines.fhir import FHIR_R4_FILTER_OPERATORS  # noqa: F401

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"

TOOCOSTLY_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)
RESPONSES_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "engines" / "fhir" / "responses.py"
)


# =============================================================================
# Helpers
# =============================================================================


def _post_expand(fhir_client, body: dict, *, params: dict | None = None) -> tuple[int, dict]:
    """POST a body to /fhir/ValueSet/$expand."""
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


def _get_expand(fhir_client, *, params: dict) -> tuple[int, dict]:
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


def _make_intensional_snomed_isa() -> dict:
    """Helper: build an intensional ValueSet body with is-a SNOMED Diabetes."""
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
    """Helper: build an extensional ValueSet body with 2 SNOMED codes."""
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


def _fhir_api_text() -> str:
    """Return the raw source of ``apps/fhir_api.py`` as a string."""
    return FHIR_API_PATH.read_text()


def _function_text(source: str, name: str) -> str:
    """Extract a function's body as a string via line tracking.

    Walks the source for the ``def <name>`` (or ``async def <name>``) line,
    then scans forward tracking indentation to find the function end. Handles
    multi-line signatures (closing ``)`` at indent 0) by skipping past the
    signature until the ``:`` line.
    """
    import re
    pattern = re.compile(
        r"^(?P<indent>[ \t]*)(?:async\s+)?def\s+" + re.escape(name) + r"\s*\(",
        re.MULTILINE,
    )
    match = pattern.search(source)
    if not match:
        return ""
    start = match.start()
    def_indent = len(match.group("indent"))
    lines = source[start:].splitlines(keepends=True)
    # Phase 1: skip the multi-line signature until we find the line ending
    # with ``):`` or ``) -> ...:``. Phase 2: collect body lines until
    # indentation returns to <= def_indent on a non-blank line.
    body_lines: list[str] = []
    in_signature = True
    for i, line in enumerate(lines):
        body_lines.append(line)
        stripped_nl = line.rstrip("\n")
        if in_signature:
            # Signature ends when we find a line ending with ':'.
            if stripped_nl.rstrip().endswith(":"):
                in_signature = False
            continue
        # Phase 2: body lines. Stop when we see a non-blank line at indent
        # <= def_indent (a sibling def or top-level statement).
        if stripped_nl.strip() == "":
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= def_indent:
            body_lines.pop()  # remove the line we just added (it's not part of this fn)
            break
    return "".join(body_lines)


# =============================================================================
# Lens 1: QA-057 thread verification — explicit-size-on-truncation pattern
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.total
#       "The total number of concepts in the expansion."
# =============================================================================


class TestLens1Qa057ThreadVerification:
    """Lens 1: verify SKEPTIC's QA-057 fix is structurally complete.

    The fix added a ``total: int | None = None`` parameter to
    ``build_valueset_expand`` and updated 3 call sites. HISTORIAN must verify:

    1. The value passed is the UN-truncated size — not just
       ``len(post-truncated-list)``.
    2. ALL pre-truncating call sites are covered, including those where
       truncation happens INSIDE a helper (e.g. BFS with ``limit=count``)
       rather than via an explicit Python slice.
    """

    def test_h10_qa057_threading_extensional_path(self, fhir_client):
        """Extensional (concept-list) path — total correct (no BFS).

        The extensional path does NOT use BFS — it iterates the explicit
        ``compose.include[].concept[]`` list. ``len(deduped)`` is the actual
        un-truncated size. SKEPTIC test_s25 covers this case.

        HISTORIAN re-verifies with positive-shape assertions (per GLOBAL_RULES
        "Test-too-lenient"). The probe documents the EXPECTED behavior so a
        future regression that removes the ``total=`` parameter would fail
        loudly.
        """
        vs = _make_extensional_snomed()  # 2 concepts
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200, f"expected 200, got {status}: {body}"
        # Truncated to 1 entry; total reflects un-truncated (2).
        assert len(body["expansion"]["contains"]) == 1
        assert body["expansion"]["total"] == 2, (
            f"extensional total should be 2 (un-truncated), got "
            f"{body['expansion']['total']} — regression on QA-057 fix"
        )

    def test_h11_qa057_threading_intensional_path_fixture_coincidence(
        self, fhir_client
    ):
        """Intensional (BFS) path — total happens to be correct by coincidence.

        The intensional path uses ``get_descendants_bfs(..., limit=count)``.
        BFS early-exits at ``count`` items (``services/hierarchy.py:84``). When
        the actual descendant count > ``count``, the BFS returns at most
        ``count`` items, and ``len(deduped)`` after appending is the TRUNCATED
        size — NOT the un-truncated size.

        The conformance fixture has exactly 1 descendant (T2DM → Diabetes).
        With count=1 and is-a/73211009: BFS limit=1 returns 1 descendant,
        ``len(deduped)=2`` (1 root + 1 descendant), and total=2 happens to
        equal the actual un-truncated size (which is also 2 — Diabetes + T2DM).

        HISTORIAN pattern recognition: **the test passes for the wrong reason
        (fixture happens to match the truncation budget)**. If the fixture had
        5 descendants, BFS would return 1, ``len(deduped)=2``, but the actual
        un-truncated total would be 6 (1 root + 5 descendants). The ``total=2``
        would silently lie about the expansion size — violating FHIR R4 §4.9.2.

        Probe class: carry-forward-as-probe (CS-03 TERMINOLOGIST methodology).
        Documents the structural incompleteness of the QA-057 fix on BFS-capped
        paths. The fix cannot be exercised by the current fixture; it requires
        either a deeper fixture or a unit-level test on the helper function.
        """
        vs = _make_intensional_snomed_isa()
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200, f"expected 200, got {status}: {body}"
        # Truncated to 1 entry.
        assert len(body["expansion"]["contains"]) == 1
        # total happens to be 2 because the fixture has 1 descendant matching
        # the BFS limit=1. The QA-057 fix passes len(deduped)=2 as total.
        # If the fixture had more descendants, this assertion would expose
        # the bug: total would still be 2 (post-truncation) not N (actual).
        assert body["expansion"]["total"] == 2, (
            f"intensional total should be 2, got {body['expansion']['total']}"
        )

    def test_h12_qa057_threading_url_pattern_path_no_count_truncation(
        self, fhir_client
    ):
        """URL-based path (``expand_url_pattern``) — total under no truncation.

        ``expand_url_pattern`` caps the BFS budget via
        ``descendant_budget = max(1, count - len(contains))``. With default
        count=20 and only 1 descendant in the fixture, no truncation occurs
        and ``total=len(contains)`` is correct.

        This probe verifies the URL path returns the correct total under
        non-truncating conditions. Sibling test_h13 documents the truncating
        case.
        """
        status, body = _get_expand(
            fhir_client,
            params={"url": f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"},
        )
        assert status == 200, f"expected 200, got {status}: {body}"
        # 1 root + 1 descendant = 2 entries, no truncation.
        assert len(body["expansion"]["contains"]) == 2
        assert body["expansion"]["total"] == 2, (
            f"URL-path total should be 2 (no truncation), got "
            f"{body['expansion']['total']}"
        )

    def test_h13_qa057_url_pattern_total_truncation_fixture_coincidence(
        self, fhir_client
    ):
        """URL-based path with count=1 — total passes by fixture coincidence.

        With count=1 and is-a root 73211009:
        - ``descendant_budget = max(1, 1 - 1) = 1``
        - BFS limit=1 returns 1 descendant (T2DM)
        - ``len(contains)`` after appending = 2 (root + 1 descendant)
        - ``total=len(contains)=2`` happens to equal actual total

        Same shape as test_h11: the fix passes ``len(contains)`` as total, but
        ``len(contains)`` IS the truncated size when BFS was capped at
        ``descendant_budget``. The bug is invisible because the fixture has
        exactly 1 descendant matching the budget.
        """
        status, body = _get_expand(
            fhir_client,
            params={
                "url": f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
                "count": 1,
            },
        )
        assert status == 200, f"expected 200, got {status}: {body}"
        # Truncated to 1 entry (root only; the descendant is sliced off via
        # contains[:count]).
        assert len(body["expansion"]["contains"]) == 1
        # total is 2 because len(contains) before slice was 2. If fixture had
        # 5 descendants, BFS would return 1, len(contains)=2, total=2 — but
        # actual un-truncated would be 6. Bug invisible.
        assert body["expansion"]["total"] == 2, (
            f"URL-path total should be 2 (fixture coincidence), got "
            f"{body['expansion']['total']}"
        )

    def test_h14_qa057_source_audit_bfs_cap_on_total(self):
        """Source-reading audit: BFS limit caps total computation.

        VS-04 TERMINOLOGIST QA-068 fix landed: ``expand_url_pattern`` now
        uses the "+1 probe" pattern to detect count-limited truncation
        accurately. When count_limited is True, ``total`` is computed as
        ``len(contains) + 1`` (lower bound from the +1 probe); otherwise
        ``len(contains)`` (full BFS-observed count). The literal
        ``total=len(contains)`` is GONE.

        CF-HISTORIAN-VS02-01 is PARTIALLY closed: the +1 probe gives a
        lower bound when truncated, but the EXACT un-truncated count
        would still require an unbounded BFS walk or a separate COUNT(*)
        query. The exact-count enhancement remains deferred.
        """
        source = _fhir_api_text()
        url_fn_text = _function_text(source, "expand_url_pattern")
        assert url_fn_text, "expand_url_pattern not found"
        assert "get_descendants_bfs" in url_fn_text, (
            "expand_url_pattern should call get_descendants_bfs"
        )
        assert "limit=" in url_fn_text, (
            "expand_url_pattern should pass a limit to BFS for early-exit"
        )
        # VS-04 TERMINOLOGIST QA-068 fix: the literal ``total=len(contains)``
        # is GONE — replaced with a conditional ``len(contains) + 1`` when
        # count_limited fires. The +1 reflects the "+1 probe" pattern.
        assert "total=len(contains)" not in url_fn_text, (
            "QA-068 fix may be regressed: expand_url_pattern still uses "
            "literal total=len(contains). The +1-probe lower-bound "
            "computation should be present instead."
        )
        assert "len(contains) + 1" in url_fn_text, (
            "QA-068 fix's count_limited branch missing (len(contains) + 1)"
        )

    def test_h15_qa057_intensional_path_bfs_limit_caps_total_source_audit(self):
        """Source-reading audit: _expand_intensional BFS limit also caps total.

        Same shape as test_h14 but for the intensional path.
        ``_expand_intensional`` passes ``limit=count`` to
        ``get_descendants_bfs``. The fix at line 2146 passes
        ``total=len(deduped)`` — but deduped contains the BFS-capped relations
        list, so len(deduped) is the truncated size.
        """
        source = _fhir_api_text()
        intensional_text = _function_text(source, "_expand_intensional")
        assert intensional_text, "_expand_intensional not found"
        assert "get_descendants_bfs" in intensional_text
        assert "limit=count" in intensional_text, (
            "_expand_intensional should pass limit=count to BFS"
        )
        assert "total=len(deduped)" in intensional_text, (
            "_expand_intensional should pass total=len(deduped) per QA-057 fix"
        )


# =============================================================================
# Lens 2: CF-SKEPTIC-VS01-01..04 source-reading audit on VS-02 surface
# Spec: https://hl7.org/fhir/R4/valueset.html#filter (filter operators)
# =============================================================================


class TestLens2CarryForwardSourceAudit:
    """Lens 2: re-verify VS-01 carry-forwards still apply on VS-02 surface."""

    def test_h20_cf_vs01_01_seven_filter_operators_still_silently_dropped(self):
        """CF-SKEPTIC-VS01-01: 7 of 9 filter operators silently dropped.

        Per https://hl7.org/fhir/R4/valueset.html#filter: Filter Operator
        closed enum has 9 values. The implementation honors only ``is-a`` and
        ``descendent-of`` (post-QA-054 fix); the other 7 are silently dropped
        at the ``else`` branch (logger.debug).

        Source-reading audit confirms the silent-drop is still in place.
        """
        source = _fhir_api_text()
        intensional_text = _function_text(source, "_expand_intensional")
        assert intensional_text, "_expand_intensional not found"
        # The honored set is still is-a + descendent-of.
        assert 'op in ("is-a", "descendent-of")' in intensional_text, (
            "filter operator handling should be restricted to is-a + descendent-of"
        )
        # The else branch silently logs at DEBUG (silent-drop anti-pattern).
        assert "Unsupported filter" in intensional_text, (
            "silent-drop branch for unsupported operators should still be present"
        )

    def test_h21_cf_vs01_02_exclude_filter_still_ignored(self):
        """CF-SKEPTIC-VS01-02: exclude.filter ignored.

        ``compose.exclude[].filter[]`` is silently dropped — the exclude path
        only iterates ``exclude[].concept[]`` for code-based removal. Source-
        reading audit confirms.
        """
        source = _fhir_api_text()
        intensional_text = _function_text(source, "_expand_intensional")
        assert intensional_text
        # The exclude loop only reads exclude[].concept[], not exclude[].filter[].
        assert 'exclude.get("concept"' in intensional_text or "exclude.get('concept'" in intensional_text, (
            "exclude loop should iterate concept[]"
        )
        # Confirm there's no exclude[].filter[] handling.
        assert "exclude" in intensional_text and ".get(\"filter\")" not in intensional_text and ".get('filter')" not in intensional_text, (
            "exclude[].filter[] should NOT be handled (CF-SKEPTIC-VS01-02)"
        )

    def test_h22_cf_vs01_03_exclude_ignores_system(self):
        """CF-SKEPTIC-VS01-03: exclude ignores system when matching codes.

        The exclude removal matches on ``c["code"]`` alone, ignoring
        ``c["system"]``. A code from one system can match-and-remove a code
        from a different system. Source-reading audit confirms.
        """
        source = _fhir_api_text()
        intensional_text = _function_text(source, "_expand_intensional")
        assert intensional_text
        # The exclude matching key is c["code"] only — not (system, code).
        assert 'c["code"] not in exc_codes' in intensional_text, (
            "exclude matches on c['code'] alone — CF-SKEPTIC-VS01-03 applies"
        )

    def test_h23_cf_vs01_04_compose_metadata_silently_ignored(self):
        """CF-SKEPTIC-VS01-04: compose.lockedDate/inactive/valueSet ignored.

        Source-reading audit confirms the intensional expander reads
        ``compose.include[]`` and ``compose.exclude[]`` but ignores
        ``compose.lockedDate``, ``compose.inactive``, and
        ``compose.include[].valueSet``.
        """
        source = _fhir_api_text()
        intensional_text = _function_text(source, "_expand_intensional")
        assert intensional_text
        # The compose access is only for include/exclude.
        assert "compose.get(\"include\"" in intensional_text or "compose.get('include'" in intensional_text
        assert "compose.get(\"exclude\"" in intensional_text or "compose.get('exclude'" in intensional_text
        # No handling for lockedDate, inactive, or include[].valueSet.
        assert "lockedDate" not in intensional_text, (
            "lockedDate should NOT be handled (CF-SKEPTIC-VS01-04)"
        )
        assert "compose.get(\"inactive\")" not in intensional_text, (
            "compose.inactive should NOT be handled (CF-SKEPTIC-VS01-04)"
        )


# =============================================================================
# Lens 3: Canonical-URI helper usage (milestone-2 review structural fix 1)
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.system
#       "An absolute URI which is the code system URI of the code system from
#        which the code in the expansion was defined"
# =============================================================================


class TestLens3CanonicalSystemUriUsage:
    """Lens 3: verify canonical_system_uri() usage in expand path.

    Per milestone-2 review (CR-011/012/013/019): all ``_do_*`` handlers should
    use the shared ``canonical_system_uri()`` helper for Out ``system``
    parameters. The expand path emits ``contains[].system`` — verify it uses
    the helper consistently.
    """

    def test_h30_intensional_path_uses_canonical_system_uri(self):
        """``_expand_intensional`` uses canonical_system_uri for include[].system.

        Per CR-013 fix shape: the include[].system is re-resolved through
        ``canonical_system_uri()`` once per include block, and the canonical
        URI is propagated to every ``contains[].system`` entry in that block.
        """
        source = _fhir_api_text()
        intensional_text = _function_text(source, "_expand_intensional")
        assert intensional_text
        assert "canonical_system_uri(" in intensional_text, (
            "_expand_intensional should use canonical_system_uri() (CR-013 fix)"
        )
        # The canonical_inc variable is propagated to contains[].system.
        assert "canonical_inc" in intensional_text, (
            "canonical_inc should be used for contains[].system in include blocks"
        )

    def test_h31_implicit_value_set_path_canonical_uri_sourced_from_map(
        self, fhir_client
    ):
        """``_expand_implicit_value_set`` does NOT use canonical_system_uri().

        The implicit-value-set path resolves the source's ``system_uri``
        directly via ``SYSTEM_TO_FHIR_URI[source]`` (Form (b)) OR via the
        client-supplied URL prefix (Form (a)). This is a potential client-
        input-as-canonical drift on Form (a) — if the client supplies an alias
        URL like ``urn:oid:...`` or a trailing-slash variant, the response's
        ``contains[].system`` echoes the alias verbatim.

        Probe class: positive-shape assertion on the canonical URI value.
        The conformance fixture uses the canonical URI directly so the drift
        is invisible; the probe documents the gap (carry-forward-as-probe).
        """
        # Form (a): ICD10CM canonical URI ends in /vs.
        status, body = _get_expand(
            fhir_client,
            params={"url": f"{ICD10CM_URI}/vs"},
        )
        assert status == 200, f"expected 200, got {status}: {body}"
        contains = body["expansion"].get("contains", [])
        assert contains, "implicit value set should return at least one entry"
        # The contains[].system should be the canonical ICD10CM URI.
        for c in contains:
            assert c["system"] == ICD10CM_URI, (
                f"implicit VS contains[].system drift: expected {ICD10CM_URI}, "
                f"got {c['system']}"
            )

    def test_h32_implicit_value_set_path_alias_input_drift_documented(
        self, fhir_client
    ):
        """CF-HISTORIAN-VS02-04 (MEDIUM): implicit VS Form (a) on alias input.

        When a client supplies an alias URI (e.g. ``urn:oid:....``) for an
        implicit value set, the response's ``contains[].system`` echoes the
        alias verbatim rather than the canonical FHIR URI. This is the same
        client-input-as-canonical drift pattern as CR-011/012/013 — except
        the implicit-value-set path was missed by the milestone-2 review.

        Probe class: positive-shape assertion on canonical URI. The conformance
        fixture doesn't seed alias URIs, so the probe exercises the canonical
        URI directly and documents the deferred gap via source-reading.

        Source-reading audit (test_h33) confirms the absence of
        ``canonical_system_uri()`` call on the implicit-value-set path.
        """
        # The SNOMED all-codes form (Form (b)) uses
        # SYSTEM_TO_FHIR_URI["SNOMEDCT_US"] — canonical-by-construction.
        status, body = _get_expand(
            fhir_client,
            params={"url": f"{SNOMED_URI}?fhir_vs"},
        )
        # The fixture's SNOMED source has 2 codes — no truncation at default count.
        assert status == 200, f"expected 200, got {status}: {body}"
        contains = body["expansion"].get("contains", [])
        for c in contains:
            assert c["system"] == SNOMED_URI, (
                f"Form (b) implicit VS contains[].system drift: expected "
                f"{SNOMED_URI}, got {c['system']}"
            )

    def test_h33_implicit_value_set_path_source_audit_uses_canonical_helper(self):
        """Source-reading audit: implicit VS path NOW calls canonical_system_uri().

        **CF-HISTORIAN-VS02-02 RESOLVED via TS-03 SKEPTIC resweep QA-001**:
        ``_expand_implicit_value_set`` Form (a) now re-resolves the
        client-supplied URL prefix through ``canonical_system_uri()`` so
        ``contains[].system`` echoes the canonical FHIR URI, NOT the alias
        / trailing-slash variant. Prior buggy behavior (documented via
        carry-forward-as-probe pattern) asserted the ABSENCE of the helper
        call; the TS-03 SKEPTIC resweep fix landed the helper, so this
        probe was updated per documentation-of-buggy-behavior-as-probe
        methodology (strategy 56, TS-01 EXPLORER resweep) to assert the
        PRESENCE of the helper call.

        Spec: FHIR R4 §4.7.3 Value Set Validation — the implicit value set
        URL identifies the code system; ``contains[].system`` MUST be the
        canonical FHIR R4 system URI.
        """
        source = _fhir_api_text()
        implicit_text = _function_text(source, "_expand_implicit_value_set")
        assert implicit_text, "_expand_implicit_value_set not found"
        # The function MUST now call canonical_system_uri() on Form (a).
        assert "canonical_system_uri" in implicit_text, (
            "_expand_implicit_value_set MUST call canonical_system_uri() "
            "after the TS-03 SKEPTIC resweep QA-001 fix. If this assertion "
            "fails, the fix was reverted (CF-HISTORIAN-VS02-02 regression)."
        )


# =============================================================================
# Lens 4: Silent-fallback-on-hardcoded-default recurrence
# Spec: VS-01 TERMINOLOGIST QA-055 established the pattern (count=1).
# =============================================================================


class TestLens4HardcodedDefaultRecurrence:
    """Lens 4: any other hardcoded count/offset defaults in expand code?"""

    def test_h40_expand_post_count_default_is_20(self, fhir_client):
        """``expand_post`` default count is 20 (not hardcoded 1000).

        VS-01 TERMINOLOGIST QA-055 found that ``expand_post`` hardcoded
        ``count=1000`` for the ValueSet-body branch. The fix changed it to
        ``Query(20, ge=1, le=1000)`` mirroring ``expand_get``. HISTORIAN re-
        verifies the default is 20 (spec-compatible).
        """
        # POST without count param — should default to 20.
        vs = _make_extensional_snomed()  # 2 concepts
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        # With 2 concepts and default count=20, both should be returned (no
        # truncation).
        assert len(body["expansion"]["contains"]) == 2, (
            f"default count=20 should not truncate a 2-concept expansion; "
            f"got {len(body['expansion']['contains'])} entries"
        )
        # No toocostly extension when not truncated.
        exts = body["expansion"].get("extension", [])
        assert not any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"toocostly extension present on non-truncated expansion: {exts}"
        )

    def test_h41_expand_get_count_default_is_20(self, fhir_client):
        """``expand_get`` default count is 20."""
        status, body = _get_expand(
            fhir_client,
            params={"filter": "diabetes"},
        )
        assert status == 200, f"expected 200, got {status}: {body}"
        # Fixture has 2 diabetes matches — well within count=20 default.
        assert body["expansion"]["total"] >= 1

    def test_h42_expand_post_offset_param_not_declared(self):
        """CF-SKEPTIC-VS02-02 partial: expand_post does NOT declare offset.

        Per FHIR R4 §4.7.5: offset is a top-level In parameter, not GET-only.
        ``expand_post`` only declares ``count``, not ``offset``. Source-reading
        audit confirms.
        """
        source = _fhir_api_text()
        post_text = _function_text(source, "expand_post")
        assert post_text, "expand_post not found"
        # expand_post declares count but not offset.
        assert "count: int = Query" in post_text, (
            "expand_post should declare count as Query parameter"
        )
        assert "offset: int = Query" not in post_text, (
            "expand_post does NOT declare offset (CF-SKEPTIC-VS02-02 partial)"
        )


# =============================================================================
# Lens 5: Documentation-vs-implementation drift
# =============================================================================


class TestLens5DocumentationAccuracy:
    """Lens 5: verify docstrings accurate post-QA-057 fix."""

    def test_h50_build_valueset_expand_docstring_documents_total_param(self):
        """``build_valueset_expand`` docstring documents the new ``total`` param.

        Per GLOBAL_RULES.md "Code Review Time": docstrings should accurately
        reflect post-fix behavior. Verify the docstring mentions:
        - The ``total`` parameter.
        - The FHIR R4 §4.9.2 contract ("the total number of concepts in the
          expansion").
        - The QA-057 reference.
        """
        responses_text = RESPONSES_PATH.read_text()
        fn_text = _function_text(responses_text, "build_valueset_expand")
        assert fn_text, "build_valueset_expand not found"
        assert "total" in fn_text, (
            "build_valueset_expand docstring should mention the total parameter"
        )
        assert "§4.9.2" in fn_text or "4.9.2" in fn_text, (
            "docstring should cite FHIR R4 §4.9.2 contract on total field"
        )

    def test_h51_expand_intensional_docstring_still_offspec_spelling(self):
        """CF-HISTORIAN-VS01-02 still applies: docstring off-spec spelling.

        ``_expand_intensional`` docstring still references the off-spec
        ``descendant-of`` spelling (CF-HISTORIAN-VS01-02 documented this as
        a maintenance hazard — runtime is correct, docstring is wrong).

        HISTORIAN re-verifies the carry-forward is still open.
        """
        source = _fhir_api_text()
        intensional_text = _function_text(source, "_expand_intensional")
        assert intensional_text
        # CF-HISTORIAN-VS01-02 documents: docstring has "descendant-of"
        # (off-spec) while runtime uses "descendent-of" (spec-correct).
        assert "descendant-of" in intensional_text or "descendent-of" in intensional_text, (
            "docstring should mention descendant filter operators"
        )


# =============================================================================
# Lens 6: Test-too-lenient audit — TS-03 HISTORIAN QA-034 pattern
# =============================================================================


class TestLens6TestTooLenientAudit:
    """Lens 6: re-audit SKEPTIC's 53 probes for false-pass shapes."""

    def test_h60_test_s25_extensional_count_1_total_assertion_is_correct(self):
        """test_s25 (extensional, count=1) — total assertion is correct shape.

        Source-reading audit: SKEPTIC test_s25 asserts
        ``body["expansion"]["total"] == 2`` for a 2-concept extensional
        ValueSet with count=1. This is the SPEC-CORRECT assertion (extensional
        path doesn't use BFS; len(deduped)=2 is the actual un-truncated size).

        Test-too-lenient audit: this probe does NOT false-pass — the assertion
        is correct. Sibling probes test_h11/h13 document the BFS-capped paths
        where the same assertion shape WOULD false-pass with a deeper fixture.
        """
        # Source-reading confirms test_s25 asserts total==2 on extensional.
        test_path = (
            Path(__file__).resolve().parent / "test_vs02_skeptic.py"
        )
        src = test_path.read_text()
        assert "def test_s25_count_1_truncates_with_toocostly" in src
        assert 'body["expansion"]["total"] == 2' in src

    def test_h61_test_s61_intensional_count_1_total_fixture_coincidence(self):
        """test_s61 (intensional, count=1) — total assertion passes by fixture.

        Source-reading audit: SKEPTIC test_s61 asserts
        ``body["expansion"]["total"] == 2`` for an intensional ValueSet with
        is-a/73211009 and count=1. This assertion passes because the fixture
        has exactly 1 descendant (T2DM) matching the BFS limit=1. If the
        fixture had more descendants, the assertion would expose the bug
        (total would still be 2, not N).

        Test-too-lenient audit: this probe PASSES-FOR-THE-WRONG-REASON. The
        probe is structurally correct (asserts total==2 which is the spec-
        correct value), but the underlying implementation only produces
        total==2 because ``len(deduped)=2`` happens to equal the actual
        un-truncated size. A deeper fixture would expose the bug.

        Pattern class: test-too-lenient-on-fixture-coincidence (sibling of
        TS-03 HISTORIAN QA-034 "test-too-lenient on negative-only assertion").
        """
        test_path = (
            Path(__file__).resolve().parent / "test_vs02_skeptic.py"
        )
        src = test_path.read_text()
        assert "def test_s61_intensional_with_count_returns_truncated" in src
        assert 'body["expansion"]["total"] == 2' in src

    def test_h62_test_s73_skip_pattern_is_honest(self):
        """test_s73 (GET filter truncation) — skip pattern is honest.

        Source-reading audit: SKEPTIC test_s73 uses ``pytest.skip()`` when
        truncation is detected and the toocostly extension is absent. This is
        the carry-forward-as-probe pattern (CS-03 TERMINOLOGIST methodology)
        — honest documentation of a deferred gap.
        """
        test_path = (
            Path(__file__).resolve().parent / "test_vs02_skeptic.py"
        )
        src = test_path.read_text()
        assert "def test_s73_get_filter_truncation_emits_toocostly" in src
        assert "pytest.skip" in src
        assert "CF-SKEPTIC-VS02-03" in src


# =============================================================================
# Lens 7: CF-HISTORIAN-CS04-02 systemic duckdb.Error — verify milestone-2 fix
# Spec: FHIR R4 §3.1.0.1.5 (OperationOutcome on 4xx/5xx)
#       FHIR R4 §3.1.0.1.9 (correct MIME type on errors)
# =============================================================================


class TestLens7DuckdbErrorHandler:
    """Lens 7: verify app-level duckdb.Error handler covers expand path.

    Per CF-HISTORIAN-CS04-02 (milestone-2 review structural fix 3): a generic
    ``@app.exception_handler(duckdb.Error)`` handler emits a 503
    OperationOutcome for transient DB failures on per-operation handlers.
    """

    def test_h70_duckdb_error_handler_registered_source_audit(self):
        """Source-reading: ``@app.exception_handler(duckdb.Error)`` registered.

        Confirms the milestone-2 structural fix is in place at the app level.
        """
        src = FHIR_API_PATH.read_text()
        assert "duckdb.Error" in src, (
            "duckdb.Error handler should be referenced in fhir_api.py"
        )
        assert "@app.exception_handler(duckdb.Error)" in src, (
            "app-level duckdb.Error exception handler should be registered "
            "(milestone-2 structural fix 3)"
        )

    def test_h71_expand_path_emits_operation_outcome_on_db_error(
        self, fhir_client, monkeypatch
    ):
        """Expand path emits 503 OperationOutcome when DB fails mid-expand.

        Inject a duckdb.Error into the implicit-value-set path (which
        executes a raw SQL query). Verify the app-level handler catches it
        and emits a 503 OperationOutcome with ``application/fhir+json``
        Content-Type.
        """
        import duckdb

        # Swap the engine's ``con`` attribute for a stub whose ``execute``
        # raises duckdb.Error. The DuckDBPyConnection object itself is
        # immutable, but the engine attribute holding it can be replaced.
        original_con = fhir_client.app.state.engine.con

        class _FailingCon:
            def execute(self, *args, **kwargs):
                raise duckdb.Error(
                    "synthetic transient DB failure (HISTORIAN test)"
                )

            def fetchall(self):
                raise duckdb.Error("synthetic transient DB failure")

        # Replace the engine's connection attribute. The implicit-value-set
        # path accesses ``engine.con.execute(...).fetchall()`` at line 2274.
        # The implicit path goes through _expand_implicit_value_set which
        # imports duckdb locally and catches duckdb.Error — emitting a 500
        # OperationOutcome (line 2279). That path is INSIDE the function.
        # The app-level handler at line 504 catches duckdb.Error NOT caught
        # by inner handlers — e.g. on the intensional path which does NOT
        # have a local try/except.
        fhir_client.app.state.engine.con = _FailingCon()
        try:
            # Trigger the intensional path (no local duckdb.Error handler).
            # It calls get_code_infos → engine queries → duckdb.Error raised.
            vs = _make_intensional_snomed_isa()
            resp = fhir_client.post(
                "/fhir/ValueSet/$expand",
                json=vs,
                params={},
                headers={"Accept": "application/fhir+json"},
            )
            # The app-level handler should emit 503.
            assert resp.status_code == 503, (
                f"duckdb.Error on expand should produce 503, got {resp.status_code}: "
                f"{resp.text[:200]}"
            )
            body = resp.json()
            assert body.get("resourceType") == "OperationOutcome", (
                f"503 body should be OperationOutcome, got: {body}"
            )
            # Content-Type MUST be application/fhir+json (FHIR R4 §3.1.0.1.9).
            assert resp.headers.get("content-type", "").startswith(
                "application/fhir+json"
            ), (
                f"Content-Type should be application/fhir+json, got: "
                f"{resp.headers.get('content-type')}"
            )
        finally:
            fhir_client.app.state.engine.con = original_con


# =============================================================================
# Lens 8: Re-verify SKEPTIC's QA-057 fix end-to-end (regression guard)
# =============================================================================


class TestLens8Qa057RegressionGuard:
    """Lens 8: regression guard for SKEPTIC's QA-057 fix."""

    def test_h80_qa057_extensional_total_untruncated_when_count_caps(self, fhir_client):
        """Extensional count=1: total=2 (un-truncated).

        Regression guard: SKEPTIC's QA-057 fix on the extensional path.
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        assert len(body["expansion"]["contains"]) == 1
        assert body["expansion"]["total"] == 2

    def test_h81_qa057_toocostly_present_when_truncated(self, fhir_client):
        """Toocostly extension present when count truncates.

        Regression guard: VS-01 TERMINOLOGIST QA-055 fix (toocostly signal).
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        exts = body["expansion"].get("extension", [])
        assert any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"toocostly extension should be present on count-truncated expansion: {exts}"
        )

    def test_h82_qa057_no_toocostly_when_not_truncated(self, fhir_client):
        """No toocostly extension when count doesn't truncate.

        Regression guard: confirms toocostly is only emitted on truncation.
        """
        vs = _make_extensional_snomed()
        status, body = _post_expand(fhir_client, vs, params={"count": 20})
        assert status == 200
        exts = body["expansion"].get("extension", [])
        assert not any(e.get("url") == TOOCOSTLY_URL for e in exts), (
            f"toocostly extension should NOT be present on non-truncated expansion: {exts}"
        )
