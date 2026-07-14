"""VS-04 HISTORIAN: pattern-match against prior bug patterns.

HISTORIAN lens: re-audit SKEPTIC's new code (``expand_url_pattern`` value
dispatch, ``_resolve_max_depth`` helper, ``_expand_intensional`` /
``_expand_implicit_value_set`` call sites) against v0.0.1 silent-fallback
patterns and the recurring bug classes from prior chunks.

Reference: docs/.ai_loop/GLOBAL_KNOWLEDGE.md "Most Reliable Bug-Finding Strategies":
  - Strategy 15: test-too-lenient audit (negative-only assertion) — TS-03 HISTORIAN QA-034.
  - Strategy 47: single-boolean-gating-multiple-concerns dispatch audit — VS-04 SKEPTIC.
  - Strategy 48: env-var-crash-on-non-numeric probe class — VS-04 SKEPTIC QA-066.
  - Strategy 41: test-too-lenient-on-fixture-coincidence — VS-02 HISTORIAN CF-HISTORIAN-VS02-01.

SKEPTIC carry-forwards to probe:
  1. ``_resolve_max_depth`` edge cases: missing env var, "0", negative, very large,
     non-integer, whitespace. (Per architect flag #2.)
  2. ``expand_url_pattern`` lines 162-203: explicit value-validation. Are ALL
     unrecognized values rejected? (Pattern-match against TS-01 SKEPTIC strategy 2
     silent-wrong-answer-on-discrete-value-params.)
  3. Single-boolean-gating-multiple-concerns dispatch (SKEPTIC pattern count=1):
     SKEPTIC noted sibling candidates in ``$validate-code?url=...`` and
     ``$lookup?url=...``. Audit.
  4. Test-too-lenient on SKEPTIC ``test_s20`` (architect flag #4 + GLOBAL_RULES
     "negative-only-assertion trap"): now that ``?fhir_vs=refset`` returns 400
     explicitly, tighten to assert status code 400.
  5. CF-SKEPTIC-VS01-01 (7 missing filter operators): doesn't apply to URL-pattern
     path. But verify no leakage.
  6. CF-HISTORIAN-VS02-01 (BFS cap on total): applies to URL-pattern path's
     ``total=len(contains)`` after BFS cap. Reconfirm.
  7. CF-HISTORIAN-VS02-02 (implicit path canonical_system_uri): URL-pattern path
     uses canonical SNOMED URI directly, so unaffected. Reconfirm.

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus) -> 44054006 (T2DM)
  - mrrel: 1 row (T2DM isa Diabetes mellitus)

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
SNOMED CT intensional: https://hl7.org/fhir/R4/snomedct.html
Truncation ext: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent (root)
SNOMED_T2DM = "44054006"  # child of 73211009

TRUNCATION_EXT_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"


def _expand_url(client, url: str, count: int | None = None):
    """Helper: GET /fhir/ValueSet/$expand with the given url (and count)."""
    params = [("url", url)]
    if count is not None:
        params.append(("count", count))
    return client.get("/fhir/ValueSet/$expand", params=params)


def _contains_codes(resp_json: dict) -> list[str]:
    return [c.get("code") for c in resp_json.get("expansion", {}).get("contains", [])]


def _extensions(resp_json: dict) -> list[dict]:
    return resp_json.get("expansion", {}).get("extension", [])


# =============================================================================
# Group 1: SKEPTIC fixes survived — regression-guard probes
# =============================================================================


class TestHistorianSkepticFixesSurvived:
    """HISTORIAN lens: re-verify the 5 SKEPTIC fixes via positive-success-shape
    assertions (per GLOBAL_RULES "negative-only-assertion trap").

    Each fix's SKEPTIC probe was the original reproducer. HISTORIAN re-probes
    with the SAME reproducer AND a positive-success-shape assertion to ensure
    the fix is structurally intact (not passing for the wrong reason).
    """

    def test_h10_qa060_unknown_value_returns_400_explicit(
        self, fhir_client
    ):
        """QA-060 fix survived: ``?fhir_vs=unknown`` returns 400 explicitly.

        SKEPTIC's test_s80 used a negative-only assertion (NOT descendants-only).
        HISTORIAN tightens to assert POSITIVE success shape: 400 +
        OperationOutcome mentioning the unrecognized value.

        Per GLOBAL_RULES "negative-only-assertion trap" (TS-03 HISTORIAN QA-034
        meta-discovery): if the impl had a different bug that produced a
        different error string, SKEPTIC's negative-only probe would still pass.
        Tighten to positive status + body shape.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=unknown",
        )
        assert resp.status_code == 400, (
            f"QA-060 fix: ?fhir_vs=unknown MUST return 400 explicitly; "
            f"got {resp.status_code}"
        )
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome"
        diag = body.get("issue", [{}])[0].get("diagnostics", "")
        assert "unknown" in diag or "Unsupported" in diag, (
            f"QA-060 fix: error MUST mention unrecognized value; got {diag!r}"
        )

    def test_h11_qa061_case_variant_recognized(self, fhir_client):
        """QA-061 fix survived: ``?fhir_vs=ISA`` recognized (case-insensitive).

        SKEPTIC's test_s81 used a negative-only assertion (NOT descendants-only).
        HISTORIAN tightens to assert the FULL isa-equivalent success shape:
        200 with root + descendants (proving the value was recognized and
        dispatched correctly).
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=ISA",
        )
        assert resp.status_code == 200, (
            f"QA-061 fix: ?fhir_vs=ISA MUST be recognized (case-insensitive); "
            f"got {resp.status_code}"
        )
        codes = _contains_codes(resp.json())
        # Must include BOTH root AND descendant (full isa semantics).
        assert SNOMED_DIABETES_MELLITUS in codes and SNOMED_T2DM in codes, (
            f"QA-061 fix: ?fhir_vs=ISA must produce full isa expansion; "
            f"got {codes}"
        )

    def test_h12_qa062_refset_returns_400_with_capability_message(
        self, fhir_client
    ):
        """QA-062 fix survived: ``?fhir_vs=refset`` returns 400 with clear
        message about missing refset data capability.

        SKEPTIC's test_s20 used a negative-only assertion (NOT both root +
        descendant present). HISTORIAN tightens to assert POSITIVE status
        code + diagnostics message. This addresses architect flag #4 and the
        "negative-only-assertion trap" pattern (PROMOTED count=1).
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=refset",
        )
        assert resp.status_code == 400, (
            f"QA-062 fix: ?fhir_vs=refset MUST return 400 (refset data not "
            f"implemented); got {resp.status_code}"
        )
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome"
        diag = body.get("issue", [{}])[0].get("diagnostics", "")
        # The error message MUST mention the missing capability (refset data)
        # so the operator / client understands why the operation is rejected.
        assert "refset" in diag.lower(), (
            f"QA-062 fix: error MUST mention refset capability gap; got {diag!r}"
        )

    def test_h13_qa065_depth_0_signals_truncation(self, fhir_client, monkeypatch):
        """QA-065 fix survived: ``FHIR_VS_MAX_DEPTH=0`` emits toocostly ext."""
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "0")
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        exts = _extensions(resp.json())
        assert any(e.get("url") == TRUNCATION_EXT_URL for e in exts), (
            "QA-065 fix: FHIR_VS_MAX_DEPTH=0 MUST emit toocostly extension; "
            f"got {exts}"
        )

    def test_h14_qa066_invalid_env_value_does_not_crash(self, fhir_client, monkeypatch):
        """QA-066 fix survived: invalid ``FHIR_VS_MAX_DEPTH`` does not crash.

        Assert 200 with default behavior (the helper falls back to default 5,
        which walks the 1-layer descendant in the fixture).
        """
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "not-a-number")
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200, (
            f"QA-066 fix: invalid env value MUST NOT crash; got {resp.status_code}"
        )
        # With default 5, both root + descendant are present.
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes and SNOMED_T2DM in codes


# =============================================================================
# Group 2: _resolve_max_depth edge case matrix
# =============================================================================


class TestHistorianResolveMaxDepthEdgeCases:
    """HISTORIAN lens: probe ``_resolve_max_depth`` against the documented
    edge-case matrix per SKEPTIC architect flag #2.

    Edge cases to probe:
      - Missing env var (None) → default 5
      - Empty string → default 5
      - "0" → 0 (recognized)
      - "-1" / "-100" → negative — BUG (silent-wrong-answer per QA-065 shape)
      - Very large integer → no upper bound check
      - Non-integer ("abc") → default 5 (QA-066 fix)
      - Whitespace ("5 ") → 5 (Python int() strips)
      - Float ("5.5") → default 5 (cannot int() a float-string)
    """

    def test_h20_missing_env_var_uses_default(self, fhir_client, monkeypatch):
        """Missing env var → default 5 (no crash, no warning)."""
        monkeypatch.delenv("FHIR_VS_MAX_DEPTH", raising=False)
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        # Default 5 walks the 1-layer descendant in the fixture.
        codes = _contains_codes(resp.json())
        assert SNOMED_T2DM in codes, (
            f"Default max_depth=5 should walk descendants; got {codes}"
        )

    def test_h21_empty_string_env_uses_default(self, fhir_client, monkeypatch):
        """Empty string env value → default 5."""
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "")
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_T2DM in codes

    def test_h22_whitespace_padded_value_accepted(self, fhir_client, monkeypatch):
        """``FHIR_VS_MAX_DEPTH=" 5 "`` → 5 (Python int() strips whitespace).

        This documents the actual behavior. Not a bug — but worth pinning so a
        future change to the helper doesn't silently break the contract.
        """
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", " 5 ")
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        # Either: int(" 5 ") succeeds (Python strips) → 200 with descendants,
        # or it fails and falls back to default 5 → 200 with descendants.
        # The test is satisfied either way as long as no crash + descendants present.
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_T2DM in codes

    def test_h23_float_string_rejected_with_default(self, fhir_client, monkeypatch):
        """``FHIR_VS_MAX_DEPTH="5.5"`` → ValueError → default 5.

        ``int("5.5")`` raises ValueError in Python (must use float() first).
        The helper catches ValueError and falls back to default. NOT a bug —
        the helper's behavior is correct. Probe documents the contract.
        """
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "5.5")
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_T2DM in codes

    def test_h24_negative_value_rejected_with_default(
        self, fhir_client, monkeypatch
    ):
        """``FHIR_VS_MAX_DEPTH=-1`` → rejected with WARNING + default fallback.

        VS-04 HISTORIAN QA-067: the prior implementation silently accepted
        negative values, which produced silent-wrong-answer in
        ``expand_url_pattern`` — the QA-065 synthesis (``if max_depth == 0``)
        only covers zero, not negatives. The fix rejects negatives in
        ``_resolve_max_depth`` with a WARNING, falling back to default 5.

        Pattern-match against QA-065 + QA-066 root cause: defensive parsing
        in ``_resolve_max_depth`` catches operator misconfigurations
        uniformly (missing / non-numeric / negative) per GLOBAL_RULES
        "Silent Fallbacks".
        """
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "-1")
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        # With default 5 (negative rejected), descendants ARE walked.
        assert SNOMED_DIABETES_MELLITUS in codes, (
            "Root must be included after negative-value fallback; "
            f"got {codes}"
        )
        assert SNOMED_T2DM in codes, (
            f"Descendants must be walked with default 5 after negative-value "
            f"rejection; got {codes}"
        )

    def test_h25_very_large_value_no_upper_bound(
        self, fhir_client, monkeypatch
    ):
        """``FHIR_VS_MAX_DEPTH=999999`` → no upper bound check (no crash).

        Pattern-match against QA-066: the helper catches ValueError for
        non-numeric strings. But there's no upper-bound check — a very large
        integer is accepted as-is. For the conformance fixture (1 mrrel row,
        depth 1), the BFS exits naturally before reaching depth 999999.
        In production with deep SNOMED subtrees, this could be a DoS surface
        if the operator sets a giant depth value. NOT a VS-04 bug — documenting
        as a finding candidate. The fix shape would be a reasonable upper
        bound (e.g. ``min(value, MAX_REASONABLE_DEPTH=50)``).
        """
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "999999")
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        # No crash; the fixture has only 1 descendant layer.
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_T2DM in codes


# =============================================================================
# Group 3: Single-boolean-gating-multiple-concerns dispatch audit (SKEPTIC
# pattern count=1; HISTORIAN extends to sibling candidates)
# =============================================================================


class TestHistorianDispatchPatternSiblings:
    """HISTORIAN lens: pattern-match the SKEPTIC "single-boolean-gating-
    multiple-concerns dispatch" pattern (count=1) against sibling candidates.

    Per SKEPTIC architect flag #5 + carry-forward note 5: the
    ``fhir_vs`` value-dispatch root cause (single boolean controlling root
    inclusion + unconditional downstream walk) may exist in sibling URL
    parsers. SKEPTIC noted candidates: ``$validate-code?url=...`` and
    ``$lookup?url=...``.

    HISTORIAN finding: NO sibling URL parsers in medterm4ds process the
    ``?fhir_vs`` intensional convention. ``$validate-code`` and ``$lookup``
    accept a ``url`` parameter but it's the ValueSet / CodeSystem canonical
    URL, NOT a SNOMED intensional shorthand. Source-reading confirms
    ``expand_url_pattern`` is the ONLY consumer of the ``fhir_vs`` query
    parameter. The pattern count remains 1 (no siblings).

    These probes document the audit and pin the invariant.
    """

    def test_h30_validate_code_does_not_process_fhir_vs_url(self, fhir_client):
        """``$validate-code?url=...?fhir_vs=isa`` does not silently dispatch
        via the intensional path.

        The ``url`` param on ``$validate-code`` is the ValueSet canonical URL
        (per FHIR R4 §4.9.7.1 In Parameters: ``url`` — "ValueSet Canonical
        URL"). It's NOT the SNOMED intensional shorthand. If a client sends
        the SNOMED intensional URL by mistake, the server should NOT silently
        produce an intensional expansion — it should return a clear error
        (the URL doesn't match a known ValueSet).
        """
        resp = fhir_client.get(
            "/fhir/ValueSet/$validate-code",
            params=[
                ("url", f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"),
                ("code", SNOMED_DIABETES_MELLITUS),
                ("system", SNOMED_URI),
            ],
        )
        # The intensional URL is NOT a ValueSet canonical URL — the impl
        # MUST NOT silently succeed by treating it as an intensional expansion.
        # Either: 200 with result reflecting "is the code in the system" (the
        # current behavior — $validate-code reduces to code-system presence),
        # or 4xx with a clear error.
        assert resp.status_code in (200, 400, 422), (
            f"$validate-code with intensional url returned {resp.status_code}"
        )
        # If 200, the response MUST be a Parameters body, not a ValueSet.
        if resp.status_code == 200:
            body = resp.json()
            assert body.get("resourceType") == "Parameters", (
                f"$validate-code MUST return Parameters, not "
                f"{body.get('resourceType')!r}"
            )

    def test_h31_lookup_does_not_process_fhir_vs_url(self, fhir_client):
        """``$lookup?url=...?fhir_vs=isa`` does not silently dispatch via
        the intensional path.

        ``$lookup`` does NOT accept a ``url`` parameter per FHIR R4
        §4.8.21.1 (In Parameters: ``system``, ``code``, ``coding``,
        ``codeableConcept``, ``version``, ``display``, ``property``,
        ``propertyDisplayLanguage``, ``useSupplement``). No ``url`` param.
        The server MUST NOT silently accept and process it.
        """
        # ``url`` is NOT in the spec — FastAPI's permissive default may accept
        # it as an extra query param and ignore. The handler MUST NOT change
        # behavior based on the intensional url.
        resp = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params=[
                ("system", SNOMED_URI),
                ("code", SNOMED_DIABETES_MELLITUS),
                ("url", f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"),
            ],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("resourceType") == "Parameters"
        # The display MUST be the canonical name, regardless of the extra
        # ``url`` query param.
        display_param = next(
            (p for p in body.get("parameter", []) if p.get("name") == "display"),
            None,
        )
        assert display_param is not None
        assert display_param.get("valueString") == "Diabetes mellitus"


# =============================================================================
# Group 4: Carry-forward reconfirmations (CF-SKEPTIC-VS01-01,
# CF-HISTORIAN-VS02-01, CF-HISTORIAN-VS02-02)
# =============================================================================


class TestHistorianCarryForwardReconfirmations:
    """HISTORIAN lens: reconfirm CF carry-forwards still apply.

    Per SKEPTIC architect flags #3 + carry-forward notes 1-3:
      - CF-SKEPTIC-VS01-01: 7 missing filter operators in
        ``_expand_intensional``. Applies to intensional ValueSet BODY path,
        NOT URL-pattern path. Verify no leakage.
      - CF-HISTORIAN-VS02-01: BFS cap on total computation. Applies to
        URL-pattern path's ``total=len(contains)`` after BFS cap.
      - CF-HISTORIAN-VS02-02: implicit path lacks canonical_system_uri.
        URL-pattern path uses canonical SNOMED URI directly (SYSTEM_TO_FHIR_URI)
        so unaffected.
    """

    def test_h40_cf_skeptic_vs01_01_no_leakage_to_url_path(self, fhir_client):
        """CF-SKEPTIC-VS01-01 (7 missing filter operators) does NOT apply to
        URL-pattern path.

        The URL-pattern path (``expand_url_pattern``) processes SNOMED CT
        intensional URLs (``?fhir_vs=isa``). The ``isa`` semantic is
        structurally equivalent to ``filter[is-a]`` in the ValueSet body path.
        The 7 missing filter operators (``=``, ``is-not-a``, ``regex``, ``in``,
        ``not-in``, ``generalizes``, ``exists``) are NOT applicable to the URL
        pattern — they're only expressible in the ValueSet body.

        Probe: verify the URL-pattern path doesn't silently accept URL forms
        that map to unsupported operators (e.g. ``?fhir_vs=is-a``).
        """
        # ``?fhir_vs=is-a`` is NOT a valid SNOMED intensional URL value.
        # The dispatch MUST reject it as unrecognized.
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=is-a",
        )
        assert resp.status_code == 400, (
            "CF-SKEPTIC-VS01-01 leakage guard: ?fhir_vs=is-a MUST be rejected "
            f"(unrecognized value); got {resp.status_code}"
        )

    def test_h41_cf_historian_vs02_01_bfs_cap_on_total(self, fhir_client):
        """CF-HISTORIAN-VS02-01 (BFS cap on total) still applies to URL-pattern
        path.

        The URL-pattern path passes ``total=len(contains)`` AFTER the
        BFS-capped relations have been appended (``contains[:count]`` is
        sliced before the build call, but ``total`` is computed BEFORE the
        slice). For the conformance fixture (1 descendant matching count=1),
        the test passes for the wrong reason.

        HISTORIAN source-reading: line 224 ``descendant_budget = max(1,
        count - len(contains))``, line 225 ``relations, depth_cap_hit =
        get_descendants_bfs(..., limit=descendant_budget)``, line 262
        ``contains[:count], url=url, ..., total=len(contains)``.

        The ``total=len(contains)`` is computed AFTER the descendants have
        been appended — so when the BFS limit fires (descendant_budget
        reached), contains may not be all of them. But the descendant_budget
        is ``count - len(contains_before_descendants)`` which equals
        ``count - 1`` (root already added). So when BFS returns
        ``descendant_budget`` relations, contains has ``count`` entries
        (root + descendants). The total IS correct when the root is the only
        pre-descendant entry.

        Wait — there's still a fixture-coincidence concern: if the root were
        NOT a real code (root_infos is None), the BFS budget would be ``count``
        and the total would still be off-by-one. Let me probe with the actual
        fixture to confirm.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        assert resp.status_code == 200
        body = resp.json()
        contains = body["expansion"]["contains"]
        total = body["expansion"]["total"]
        # Fixture: 1 root + 1 descendant = 2 entries un-truncated.
        # count=1 truncates to 1 entry. total MUST reflect un-truncated size (2).
        # When count=1, descendant_budget = max(1, 1-1) = 1. BFS returns up to 1
        # relation. contains has 1 (root) + 1 (descendant) = 2 entries BEFORE
        # the [:count] slice. So total=len(contains)=2 IS correct here.
        # This probe PASSES because the fixture happens to have exactly 1
        # descendant matching the BFS limit=1.
        assert total == 2, (
            "CF-HISTORIAN-VS02-01 fixture-coincidence probe: total should "
            f"reflect un-truncated size (2); got {total}. NOTE: this probe "
            "passes for the wrong reason when the fixture has exactly 1 "
            "descendant matching the BFS limit=1. See CF-HISTORIAN-VS02-01."
        )

    def test_h42_cf_historian_vs02_02_url_pattern_uses_canonical_snomed(
        self, fhir_client
    ):
        """CF-HISTORIAN-VS02-02 (implicit path lacks canonical_system_uri)
        does NOT apply to URL-pattern path.

        The URL-pattern path (``expand_url_pattern``) sources the SNOMED URI
        from ``SYSTEM_TO_FHIR_URI["SNOMEDCT_US"]`` (line 173). This is the
        canonical map — the value IS canonical. No drift.

        Probe: verify every contains[].system in the response is the canonical
        SNOMED URI, not an alias.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        contains = resp.json()["expansion"]["contains"]
        for c in contains:
            assert c.get("system") == SNOMED_URI, (
                f"URL-pattern contains[].system MUST be canonical SNOMED URI "
                f"({SNOMED_URI}); got {c.get('system')!r}"
            )

    def test_h43_cf_historian_vs02_02_alias_input_resolved_to_canonical(
        self, fhir_client
    ):
        """URL-pattern path with ALIAS SNOMED URI input resolves contains[]
        to canonical.

        The dispatch recognizes the SNOMED URI via substring match (``snomed_uri
        in base``). If a client uses an alias (e.g. trailing slash variant
        ``http://snomed.info/sct/``), the substring match succeeds but the
        returned contains[].system is sourced from ``SYSTEM_TO_FHIR_URI``
        (canonical). So no client-input-as-canonical drift.

        This is the structural guarantee CF-HISTORIAN-VS02-02 lacks on the
        implicit path. Probe documents the difference.
        """
        # Trailing-slash variant of SNOMED URI
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}/?fhir_vs=isa",
        )
        # Either 200 (substring match succeeds) or 400 (parser rejects
        # trailing slash). Either way, contains[].system is canonical.
        if resp.status_code == 200:
            contains = resp.json()["expansion"]["contains"]
            for c in contains:
                assert c.get("system") == SNOMED_URI, (
                    f"URL-pattern contains[].system MUST be canonical even "
                    f"on alias input; got {c.get('system')!r}"
                )


# =============================================================================
# Group 5: Additional HISTORIAN probes — unrecognized value dispatch
# exhaustiveness
# =============================================================================


class TestHistorianUnrecognizedValueExhaustiveness:
    """HISTORIAN lens: verify EVERY unrecognized ``fhir_vs`` value is rejected.

    Per SKEPTIC strategy 47 (single-boolean-gating-multiple-concerns dispatch
    audit): the fix centralizes the dispatch via ``fhir_vs_normalized not in
    ("", "isa", "refset")``. HISTORIAN probes the exhaustiveness by trying
    near-miss values that a buggy future change might silently accept.
    """

    @pytest.mark.parametrize("value,should_pass", [
        ("isa", True),            # canonical — must succeed
        ("ISA", True),            # case-variant — must succeed (QA-061)
        ("Isa", True),            # mixed-case — must succeed (QA-061)
        ("refset", "error"),      # recognized but unimplemented — must 400 (QA-062)
        ("REFSET", "error"),      # case-variant of refset — must 400
        ("", True),               # bare — equivalent to isa
        ("isa=isa", False),       # typo with extra '=' — must reject
        ("descendants", False),   # common-English near-homograph — must reject
        ("children", False),      # SNOMED relationship type as value — must reject
        ("parents", False),       # SNOMED relationship type as value — must reject
        ("all", False),           # generic near-homograph — must reject
        ("*", False),             # wildcard — must reject
        ("true", False),          # boolean-style value — must reject
        ("false", False),         # boolean-style value — must reject
        ("null", False),          # null literal — must reject
        ("none", False),          # none literal — must reject
    ])
    def test_h50_fhir_vs_value_dispatch_exhaustiveness(
        self, fhir_client, value, should_pass
    ):
        """``?fhir_vs={value}`` dispatch is exhaustive."""
        url = f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs={value}"
        resp = _expand_url(fhir_client, url)
        if should_pass is True:
            assert resp.status_code == 200, (
                f"?fhir_vs={value!r} MUST succeed (recognized value); "
                f"got {resp.status_code}"
            )
            codes = _contains_codes(resp.json())
            assert SNOMED_DIABETES_MELLITUS in codes, (
                f"?fhir_vs={value!r} MUST include root; got {codes}"
            )
        elif should_pass == "error":
            assert resp.status_code == 400, (
                f"?fhir_vs={value!r} MUST return 400 (unimplemented); "
                f"got {resp.status_code}"
            )
        else:  # should_pass is False → reject
            assert resp.status_code == 400, (
                f"?fhir_vs={value!r} MUST be rejected (unrecognized value); "
                f"got {resp.status_code}. SILENT-WRONG-ANSWER if 200 with "
                "partial content."
            )


# =============================================================================
# Group 6: _resolve_max_depth location audit (architect flag #2)
# =============================================================================


class TestHistorianResolveMaxDepthLocation:
    """HISTORIAN lens: audit ``_resolve_max_depth`` location (module scope).

    Per SKEPTIC architect flag #2: the helper is at module scope in
    ``apps/fhir_api.py``. SKEPTIC noted: "If VS-05/CM-* need it, promote to
    core.config or engines.fhir". HISTORIAN confirms:

      - 3 call sites in apps/fhir_api.py: ``expand_url_pattern`` (line 223),
        ``_expand_intensional`` (line 2121), ``_expand_implicit_value_set``
        (line 2406).
      - No other module imports ``_resolve_max_depth`` (confirmed via grep).
      - The helper is a private function (leading underscore) — NOT a public
        API surface. Module-scope is the correct location.

    Probe class: source-reading probes that confirm the helper signature +
    location are stable (pin the contract).
    """

    def test_h60_resolve_max_depth_signature_and_location(self):
        """``_resolve_max_depth`` is a module-scope helper in apps.fhir_api.

        Source-reading probe: import the module and verify the helper exists
        at module scope with the expected signature. Future refactors that
        move the helper (or rename it) MUST update this probe.
        """
        from medterm4ds.apps.fhir_api import _resolve_max_depth
        import inspect

        # Helper exists and is callable.
        assert callable(_resolve_max_depth)

        # Signature: ``default: int = 5`` (single optional param).
        sig = inspect.signature(_resolve_max_depth)
        params = list(sig.parameters.keys())
        assert params == ["default"], (
            f"_resolve_max_depth signature MUST be (default=5); got {params}"
        )
        # Default value is 5 (matches the docstring and SKEPTIC FIX-005).
        assert sig.parameters["default"].default == 5, (
            f"_resolve_max_depth default MUST be 5; "
            f"got {sig.parameters['default'].default}"
        )

    def test_h61_resolve_max_depth_no_call_to_int_os_getenv_in_body(self):
        """``_resolve_max_depth`` body uses defensive parsing (no direct
        ``int(os.getenv(...))``).

        Source-reading probe: the SKEPTIC FIX-005 introduced the helper to
        replace 3 sites of ``int(os.getenv("FHIR_VS_MAX_DEPTH", "5"))``. The
        helper's body MUST catch ``ValueError`` (per GLOBAL_RULES "Silent
        Fallbacks"). Verify the source still implements this.
        """
        import inspect
        from medterm4ds.apps.fhir_api import _resolve_max_depth

        src = inspect.getsource(_resolve_max_depth)
        # The body MUST have a try/except (TypeError, ValueError) block.
        assert "TypeError" in src and "ValueError" in src, (
            "_resolve_max_depth MUST catch both TypeError and ValueError "
            "(per SKEPTIC FIX-005 and GLOBAL_RULES 'Silent Fallbacks')."
        )
        # The body MUST log at WARNING (not DEBUG) when falling back.
        assert "logger.warning" in src, (
            "_resolve_max_depth MUST log at WARNING (not DEBUG) when "
            "falling back to default. Per GLOBAL_RULES 'Silent Fallbacks'."
        )


# =============================================================================
# Group 7: Total computation audit on URL-pattern path (CF-HISTORIAN-VS02-01
# source-reading refinement)
# =============================================================================


class TestHistorianTotalComputationSourceAudit:
    """HISTORIAN lens: source-read the ``total=`` computation on the URL-pattern
    path to refine CF-HISTORIAN-VS02-01.

    CF-HISTORIAN-VS02-01 noted: "the ``total=`` parameter passed is
    ``len(contains)``/``len(deduped)`` AFTER the BFS-capped relations have
    been appended — so total IS the truncated size when BFS was capped."

    HISTORIAN source-reading of ``expand_url_pattern``:

    Line 213: ``contains: list[dict[str, Any]] = []``
    Line 214-221: root entry appended (1 entry).
    Line 223: ``max_depth = _resolve_max_depth()``
    Line 224: ``descendant_budget = max(1, count - len(contains))`` → ``count - 1``
    Line 225-230: BFS with ``limit=descendant_budget`` (early-exits at budget).
    Line 231-236: descendants appended to contains.
    Line 262: ``return build_valueset_expand(contains[:count], url=url, ...,
              total=len(contains))``.

    When BFS budget fires (returns ``descendant_budget`` relations):
      - contains has 1 (root) + descendant_budget entries.
      - ``len(contains)`` = 1 + (count - 1) = count.
      - ``total=count`` is the TRUNCATED size, NOT the un-truncated size.

    The CF-HISTORIAN-VS02-01 finding IS correct: the URL-pattern path suffers
    the same bug as the intensional path. The bug is invisible in CI because
    the fixture has exactly 1 descendant matching count=1's budget=0...

    Wait — let me recompute. With count=1:
      - descendant_budget = max(1, 1 - 1) = max(1, 0) = 1.
      - BFS returns up to 1 relation.
      - contains has 1 (root) + 1 (descendant) = 2 entries.
      - ``total = len(contains) = 2`` (correct un-truncated size by coincidence).

    The CF-HISTORIAN-VS02-01 fixture-coincidence IS real: with the current
    fixture (1 descendant), count=1 produces the correct total by accident.
    A deeper fixture would expose the bug.
    """

    def test_h70_total_correct_on_current_fixture(self, fhir_client):
        """``total`` reflects un-truncated size on current fixture (count=1).

        This probe PASSES because the fixture has exactly 1 descendant
        matching the BFS limit (descendant_budget=1). Per
        CF-HISTORIAN-VS02-01, this is a fixture-coincidence — the structural
        bug exists but is invisible.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        assert resp.status_code == 200
        body = resp.json()
        # Fixture has 2 codes (root + 1 descendant). count=1 → contains=1,
        # total MUST be 2 (un-truncated).
        assert body["expansion"]["total"] == 2

    def test_h71_total_correct_on_count_2_no_truncation(self, fhir_client):
        """``total`` reflects un-truncated size when no truncation fires
        (count=2).

        No fixture-coincidence here: count=2 doesn't trigger BFS cap, so the
        total computation is structurally correct.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=2,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["expansion"]["total"] == 2
        assert len(body["expansion"]["contains"]) == 2

    def test_h72_total_source_audit(self):
        """Source-reading probe: ``expand_url_pattern`` total computation.

        VS-04 TERMINOLOGIST QA-068 fix landed: the total is now computed
        conditionally — when count_limited, ``total = len(contains) + 1``
        (lower bound from the +1 probe); when not count_limited,
        ``total = len(contains)`` (full size). The structural CF-HISTORIAN-
        VS02-01 finding (BFS cap on total) is PARTIALLY closed: the +1 probe
        gives a lower bound, but the EXACT un-truncated count would still
        require an unbounded BFS walk. CF-HISTORIAN-VS02-01 remains open
        for the exact-count enhancement.
        """
        import inspect
        from medterm4ds.apps.fhir_api import expand_url_pattern

        src = inspect.getsource(expand_url_pattern)
        # The QA-068 fix introduced a conditional total computation. The
        # prior literal ``total=len(contains)`` is GONE — replaced with an
        # if/else that adds 1 when count_limited fires.
        assert "total=len(contains)" not in src, (
            "QA-068 fix may be regressed: expand_url_pattern still uses "
            "literal total=len(contains). The +1-probe lower-bound "
            "computation should be present instead."
        )
        # The fix's two branches must be visible in the source.
        assert "len(contains) + 1" in src, (
            "QA-068 fix's count_limited branch missing (len(contains) + 1)"
        )


# =============================================================================
# Group 8: Defensive — verify refset response is FHIR-shaped (not raw 500)
# =============================================================================


class TestHistorianRefsetResponseShape:
    """HISTORIAN lens: verify the ``?fhir_vs=refset`` 400 response is a proper
    FHIR OperationOutcome (not a raw Python error or text/plain).

    This addresses the "alternative-failure-path probe at error-isolation
    boundary" pattern (TS-04 HISTORIAN QA-038 strategy 18).
    """

    def test_h80_refset_response_is_operationoutcome(self, fhir_client):
        """``?fhir_vs=refset`` response MUST be a FHIR OperationOutcome."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=refset",
        )
        assert resp.status_code == 400
        # Content-Type MUST be FHIR MIME (not text/plain for raw traceback).
        ct = resp.headers.get("content-type", "")
        assert "application/fhir+json" in ct, (
            f"Refset error response MUST be application/fhir+json; got {ct!r}"
        )
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome"
        issue = body.get("issue", [{}])[0]
        # Per FHIR R4 OperationOutcome: severity MUST be one of fatal/error/
        # warning/information.
        assert issue.get("severity") in ("fatal", "error", "warning"), (
            f"OperationOutcome severity MUST be valid; got {issue.get('severity')!r}"
        )
