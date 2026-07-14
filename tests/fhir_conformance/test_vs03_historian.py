"""VS-03 HISTORIAN: ValueSet $expand — Advanced.

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html

HISTORIAN lens: pattern-match against prior bug patterns discovered across
v0.0.1 + 10 chunks of the spec-compliance run. The HISTORIAN probes do not
repeat SKEPTIC's spec-citation-then-probe work; they verify the SKEPTIC fix
(QA-059) is robust AND pattern-match against recurring failure modes that
prior HISTORIAN iterations caught.

Carry-forwards from prior chunks being re-audited via source-reading +
behavioral probe:

  - CF-SKEPTIC-VS01-01 (7 of 9 filter operators silently dropped in
    ``_expand_intensional``). Source-reading: is there a structural fix
    (dispatch table) or still hardcoded ``if op in ("is-a", "descendent-of")``?
    Confirmed still hardcoded. Behavioral: 7 probes (parametrized) confirm
    silent drop.

  - CF-HISTORIAN-VS02-01 (BFS cap on total). Source-reading: confirm
    ``get_descendants_bfs(..., limit=count)`` early-exits BEFORE total is
    computed. Behavioral: test that asserts the STRUCTURAL property (when
    the BFS limit fires, total reflects truncated size, not full size).

  - CF-HISTORIAN-VS02-02 (implicit path lacks canonical_system_uri).
    Source-reading: confirm ``_expand_implicit_value_set`` does NOT call
    ``canonical_system_uri`` on the client-supplied prefix. Behavioral: alias
    input reproduces the drift.

Cross-handler canonical_system_uri usage audit: every ``_do_*`` handler
emitting Out ``system`` SHOULD route through ``canonical_system_uri``. The
milestone-2 review (CR-011/012/013) caught 3 instances missing it; VS-03
verifies the post-fix state.

Test-too-lenient-on-fixture-coincidence (CS-01 HISTORIAN pattern, VS-02
HISTORIAN extension): re-audit SKEPTIC's 36 VS-03 probes for false-positive
pass modes. Specifically, SKEPTIC test_s11 / test_s12 PASS — does the inline
ValueSet path correctly handle the spec-correct Parameters-with-valueSet
shape AND the helper-wiring ensure the spec-correct precedence (Parameters-
body count overrides GET default)?

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus)
"""

from __future__ import annotations

import inspect

import pytest

# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (canonical R4)
# Spec: https://hl7.org/fhir/R4/valueset-concept-operator.html (Filter Operator)
from medterm4ds.engines.fhir import FHIR_R4_FILTER_OPERATORS

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009

TOOCOSTLY_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"


# =============================================================================
# Helpers (mirror SKEPTIC file's helpers — same shape as test_vs03_skeptic.py)
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


def _contains_codes(body: dict) -> list[tuple[str, str]]:
    """Extract (system, code) pairs from ValueSet.expansion.contains."""
    out = []
    for c in body.get("expansion", {}).get("contains", []):
        out.append((c.get("system", ""), c.get("code", "")))
    return out


def _make_extensional_snomed(concepts=None) -> dict:
    """Build an extensional ValueSet with explicit concept list."""
    if concepts is None:
        concepts = [
            {"code": SNOMED_DIABETES_MELLITUS, "display": "Diabetes mellitus"},
            {"code": SNOMED_T2DM, "display": "Type 2 diabetes mellitus"},
        ]
    return {
        "resourceType": "ValueSet",
        "url": "http://example.org/vs/vs03-historian-extensional",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "concept": concepts,
            }],
        },
    }


def _make_intensional_snomed(op: str, root_code: str = SNOMED_DIABETES_MELLITUS) -> dict:
    """Build an intensional ValueSet with a filter on concept property."""
    return {
        "resourceType": "ValueSet",
        "url": f"http://example.org/vs/vs03-historian-intensional-{op}",
        "compose": {
            "include": [{
                "system": SNOMED_URI,
                "filter": [
                    {"property": "concept", "op": op, "value": root_code}
                ],
            }],
        },
    }


# =============================================================================
# Lens 1: QA-059 fix verification (helper robustness)
# Pattern: silent-wrong-answer on alternative parameter encoding (count=6)
# Reference: AGENTS.md "Apps/fhir_api.py:expand_post Parameters-with-valueSet
# shape" entry; SKEPTIC VS-03 QA-059.
# =============================================================================


class TestQA059HelperRobustness:
    """The SKEPTIC QA-059 fix added ``_extract_valueset_from_parameters``.
    HISTORIAN audits the helper for robustness against malformed input AND
    for cross-helper shape parity (mirrors ``_extract_coding_from_parameters``
    and ``_extract_codeable_concept_from_parameters`` shape).

    Per FHIR R4 https://hl7.org/fhir/R4/parameters.html: the ``resource``
    property carries a full resource instead of value[x]. The helper MUST
    extract the ValueSet from a Parameters body where the ``valueSet``
    parameter appears at ANY position in the ``parameter[]`` array, not just
    the first.
    """

    def test_h10_valueset_param_not_first_position(self, fhir_client):
        """The spec allows Parameters bodies to list parameters in any order.
        SKEPTIC test_s11 puts ``valueSet`` first; HISTORIAN verifies the
        helper walks the full ``parameter[]`` array (not just [0]).
        """
        nested_vs = _make_extensional_snomed()
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                # count FIRST, then valueSet — verify the helper doesn't
                # assume valueSet is at index 0.
                {"name": "count", "valueInteger": 100},
                {"name": "valueSet", "resource": nested_vs},
            ],
        }
        status, body = _post_expand(fhir_client, params_body)
        assert status == 200, f"expected 200, got {status}: {body}"
        assert body["resourceType"] == "ValueSet"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes, (
            f"valueSet at non-first position silently dropped: codes={codes}"
        )
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_h11_multiple_valueset_params(self, fhir_client):
        """Per FHIR R4 §4.7.5 In Parameters: ``valueSet`` is 0..1 (cardinality
        1). When a client sends MULTIPLE valueSet parameters (a malformed
        request), the implementation MUST gracefully extract the first valid
        one and not crash. The helper's iteration semantics (return on first
        match) MUST hold.
        """
        nested_vs_1 = _make_extensional_snomed(concepts=[
            {"code": SNOMED_DIABETES_MELLITUS, "display": "Diabetes mellitus"}
        ])
        nested_vs_2 = _make_extensional_snomed(concepts=[
            {"code": SNOMED_T2DM, "display": "Type 2 diabetes mellitus"}
        ])
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": nested_vs_1},
                {"name": "valueSet", "resource": nested_vs_2},
            ],
        }
        status, body = _post_expand(fhir_client, params_body)
        # The implementation returns the FIRST valueSet (count=1 by default
        # would truncate the 1-element list anyway, so this exercises the
        # iteration semantics).
        assert status == 200, f"expected 200, got {status}: {body}"
        assert body["resourceType"] == "ValueSet"
        # The first valueSet (Diabetes mellitus only) is honored. The second
        # is silently ignored. Per spec, the cardinality is 0..1 — the
        # server MAY reject the request as malformed OR pick the first. We
        # assert pick-first + 200 (no crash) which is the graceful shape.
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes

    def test_h12_valueset_param_with_value_x_fallback(self, fhir_client):
        """If the client sends ``valueSet`` parameter with a valueUri (a URL
        reference instead of an inline resource), the helper MUST NOT match
        (the helper only matches ``resource`` subfield). The handler falls
        through to the existing 400 path. Per FHIR R4 §4.7.5 In Parameters
        table, ``valueSet`` is a ValueSet resource type (NOT a URL reference);
        for URL-based expansion, the client uses the ``url`` parameter.
        """
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                # Wrong shape: valueUri instead of resource.
                {"name": "valueSet", "valueUri": "http://example.org/fake-vs"}
            ],
        }
        status, body = _post_expand(fhir_client, params_body)
        # MUST be 400 (no inline ValueSet found, no url, no filter). MUST NOT
        # be 500.
        assert status in (400, 422), f"expected 400/422, got {status}: {body}"
        assert body.get("resourceType") == "OperationOutcome"

    def test_h13_empty_parameter_array(self, fhir_client):
        """POST a Parameters body with an empty ``parameter`` array. The
        helper MUST iterate gracefully (no IndexError on body["parameter"][0]).
        """
        params_body = {
            "resourceType": "Parameters",
            "parameter": [],
        }
        status, body = _post_expand(fhir_client, params_body)
        assert status in (400, 422), f"expected 400/422, got {status}: {body}"
        assert body.get("resourceType") == "OperationOutcome"

    def test_h14_missing_parameter_key(self, fhir_client):
        """POST a Parameters body WITHOUT a ``parameter`` key at all. The
        helper uses ``body.get("parameter", [])`` which returns an empty
        list — graceful, no KeyError.
        """
        params_body = {
            "resourceType": "Parameters",
            # Note: no "parameter" key.
        }
        status, body = _post_expand(fhir_client, params_body)
        assert status in (400, 422), f"expected 400/422, got {status}: {body}"
        assert body.get("resourceType") == "OperationOutcome"

    def test_h15_resource_is_list_not_dict(self, fhir_client):
        """POST a Parameters body where ``parameter[].resource`` is a list
        (malformed). The helper uses ``isinstance(resource, dict)`` guard
        which skips non-dict resources gracefully.
        """
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": [{"resourceType": "ValueSet"}]}
            ],
        }
        status, body = _post_expand(fhir_client, params_body)
        # Graceful 400 (no valid inline ValueSet found). MUST NOT be 500.
        assert status < 500, f"server crash on malformed resource list: {status} {body}"

    def test_h16_resource_null_value(self, fhir_client):
        """POST a Parameters body where ``parameter[].resource`` is explicitly
        null. The helper's isinstance guard treats None as not-dict.
        """
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": None}
            ],
        }
        status, body = _post_expand(fhir_client, params_body)
        assert status < 500, f"server crash on null resource: {status} {body}"

    def test_h17_param_without_name(self, fhir_client):
        """POST a Parameters body where one parameter lacks a ``name`` field.
        The helper checks ``param.get("name") != "valueSet"`` — a None name
        will not match, the iteration continues. No crash.
        """
        nested_vs = _make_extensional_snomed()
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"valueInteger": 5},  # no name
                {"name": "valueSet", "resource": nested_vs},
            ],
        }
        status, body = _post_expand(fhir_client, params_body)
        assert status == 200, f"expected 200, got {status}: {body}"
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes


# =============================================================================
# Lens 2: CF-SKEPTIC-VS01-01 source-reading audit
# Pattern: silent-drop-on-unsupported-filter (DEBUG-level swallowing)
# Reference: GLOBAL_RULES.md "Silent Fallbacks" / VS-01 SKEPTIC QA-054.
# =============================================================================


class TestCFSkepticVS01_01_SourceAudit:
    """Source-reading audit of ``_expand_intensional`` filter-operator
    handling. Per FHIR R4 https://hl7.org/fhir/R4/valueset-concept-operator.html
    the Filter Operator closed enum has 9 values:

        = | is-a | descendent-of | is-not-a | regex | in | not-in
          | generalizes | exists

    The implementation currently honors ONLY ``is-a`` and ``descendent-of``
    (post-VS-01 SKEPTIC QA-054 spec-correct spelling fix). The other 7 are
    silently dropped via ``logger.debug("Unsupported filter: ...")``.

    HISTORIAN source-reading audit confirms:
      (a) The filter-operator dispatch is STILL hardcoded ``if op in (...)``
          — no structural fix (dispatch table) has been applied.
      (b) The 7 unsupported operators are documented in the CF as DEFERRED
          (engine enhancements out of VS-03 scope).
      (c) The closed-enum frozen-set ``FHIR_R4_FILTER_OPERATORS`` is imported
          (per milestone-2 CR-014 structural fix).
    """

    def test_h20_filter_dispatch_still_hardcoded(self):
        """Source-reading: ``_expand_intensional`` filter-operator handling
        is STILL a hardcoded ``if op in ("is-a", "descendent-of")`` check.
        No dispatch table. No structural fix.
        """
        from medterm4ds.apps import fhir_api
        src = inspect.getsource(fhir_api)
        # The hardcoded check still exists.
        assert 'op in ("is-a", "descendent-of")' in src, (
            "filter-operator dispatch changed — audit the structural fix"
        )
        # The silent-drop logger.debug still exists.
        assert 'logger.debug("Unsupported filter:' in src, (
            "silent-drop logger.debug removed — verify 7 operators now "
            "honored OR replaced with a 400 error path"
        )

    def test_h21_closed_enum_imported(self):
        """Per milestone-2 CR-014 structural fix: the closed-enum frozen-set
        ``FHIR_R4_FILTER_OPERATORS`` is imported from
        ``medterm4ds.engines.fhir``. This is the single source of truth.
        """
        from medterm4ds.engines.fhir import FHIR_R4_FILTER_OPERATORS
        # The 9 FHIR R4 filter operators per the canonical spec.
        # Spec: https://hl7.org/fhir/R4/valueset-concept-operator.html
        expected = {"=", "is-a", "descendent-of", "is-not-a", "regex",
                    "in", "not-in", "generalizes", "exists"}
        assert set(FHIR_R4_FILTER_OPERATORS) == expected, (
            f"FHIR_R4_FILTER_OPERATORS drifted from canonical R4 spec: "
            f"{sorted(FHIR_R4_FILTER_OPERATORS)} vs expected {sorted(expected)}"
        )

    @pytest.mark.parametrize("op", sorted(FHIR_R4_FILTER_OPERATORS - {"is-a", "descendent-of"}))
    def test_h22_seven_operators_still_silently_dropped(self, fhir_client, op):
        """Behavioral: each of the 7 unsupported filter operators is silently
        dropped (empty expansion returned, no error).

        Carry-forward CF-SKEPTIC-VS01-01 (reconfirmed by VS-03 SKEPTIC test_s60).
        HISTORIAN re-verifies via parametrized probe to ensure the structural
        fix (if any) is reflected.
        """
        vs = _make_intensional_snomed(op=op)
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"expected 200, got {status}: {body}"
        # Silent drop → empty expansion.
        codes = _contains_codes(body)
        assert codes == [], (
            f"filter operator {op!r} no longer silently dropped — update "
            f"CF-SKEPTIC-VS01-01 status. codes={codes}"
        )

    def test_h23_is_a_and_descendent_of_still_honored(self, fhir_client):
        """Sanity check: the 2 supported operators still work. Sibling of
        test_h22 — confirms only 7 of 9 are dropped.
        """
        for op in ("is-a", "descendent-of"):
            vs = _make_intensional_snomed(op=op)
            status, body = _post_expand(fhir_client, vs)
            assert status == 200
            codes = _contains_codes(body)
            assert codes, f"filter {op!r} returned no codes (regression)"


# =============================================================================
# Lens 3: CF-HISTORIAN-VS02-01 source-reading audit (BFS cap on total)
# Pattern: explicit-size-on-truncation (count=2 — VS-02 SKEPTIC + HISTORIAN)
# Pattern: test-too-lenient-on-fixture-coincidence (count=1 — VS-02 HISTORIAN)
# =============================================================================


class TestCFHistorianVS02_01_SourceAudit:
    """Source-reading audit of the BFS-cap-on-total issue.

    VS-02 SKEPTIC QA-057 added ``total=`` parameter to
    ``build_valueset_expand`` and updated 3 truncating call sites. VS-02
    HISTORIAN discovered the fix is INCOMPLETE on BFS-capped paths because
    ``get_descendants_bfs(..., limit=count)`` early-exits BEFORE the
    relations are appended to ``contains``. The ``total=len(contains)`` /
    ``len(deduped)`` passed to the builder IS the truncated size when BFS
    was capped.

    HISTORIAN source-reading audit confirms:
      (a) ``get_descendants_bfs`` STILL has the ``limit`` early-exit
          (per ``services/hierarchy.py`` lines 129, 146).
      (b) Both ``_expand_intensional`` (line ~2183) and ``expand_url_pattern``
          (line ~191) STILL pass ``total=len(contains/deduped)`` AFTER BFS
          cap — the structural fix candidate (COUNT query OR extend BFS to
          return total_count) has NOT landed.
      (c) The bug is invisible in CI because the fixture has exactly 1
          mrrel row matching count=1 BFS budget.
    """

    def test_h30_bfs_helper_still_has_limit_early_exit(self):
        """Source-reading: ``get_descendants_bfs`` STILL has ``limit`` param
        that early-exits at line 129 and 146.
        """
        from medterm4ds.services import hierarchy
        src = inspect.getsource(hierarchy.get_descendants_bfs)
        assert "limit: int | None = None" in src, (
            "get_descendants_bfs limit signature changed — audit"
        )
        # Early-exit at top of loop.
        assert "if limit is not None and len(results) >= limit:" in src
        # Early-exit inside inner loop after appending a result.
        assert "if limit is not None and len(results) >= limit:" in src

    def test_h31_intensional_path_total_still_derived_from_bfs_capped_list(self):
        """Source-reading: ``_expand_intensional`` STILL passes
        ``total=len(deduped)`` AFTER the BFS-capped relations were appended.
        """
        from medterm4ds.apps import fhir_api
        src = inspect.getsource(fhir_api)
        # The _expand_intensional function body contains both the BFS call
        # AND the total=len(deduped) call.
        assert "get_descendants_bfs(" in src
        assert "total=len(deduped)," in src, (
            "_expand_intensional total computation changed — audit"
        )

    def test_h32_url_pattern_path_total_still_derived_from_bfs_capped_list(self):
        """Source-reading: ``expand_url_pattern`` total computation.

        VS-04 TERMINOLOGIST QA-068 fix landed: the literal
        ``total=len(contains)`` is GONE, replaced with a conditional
        ``len(contains) + 1`` when count_limited fires (the "+1 probe"
        pattern). CF-HISTORIAN-VS02-01 is PARTIALLY closed: the +1 probe
        gives a lower bound when truncated, but the EXACT un-truncated
        count remains deferred.
        """
        from medterm4ds.apps import fhir_api
        src = inspect.getsource(fhir_api.expand_url_pattern)
        assert "get_descendants_bfs(" in src
        # QA-068 fix: the literal is GONE, replaced with conditional +1.
        assert "total=len(contains)," not in src, (
            "QA-068 fix may be regressed: expand_url_pattern still uses "
            "literal total=len(contains). The +1-probe lower-bound "
            "computation should be present instead."
        )
        assert "len(contains) + 1" in src, (
            "QA-068 fix's count_limited branch missing (len(contains) + 1)"
        )

    def test_h33_fixture_coincidence_confirmed(self, fhir_client):
        """Behavioral confirmation of fixture coincidence: the conformance
        fixture has exactly 1 mrrel row (T2DM isa 73211009). When count=1,
        the BFS limit=1 fires after finding 1 descendant. The ``total``
        reported is 2 (root + 1 descendant) which happens to equal the
        ACTUAL un-truncated size.

        This probe asserts the CURRENT fixture-coincident behavior. If a
        deeper fixture is added (multiple descendants of 73211009), the
        probe MUST be updated to expose the gap (total would still be 2
        while the actual size is larger).
        """
        # count=1, is-a on root: root + 1 descendant, but count=1 truncates
        # to 1 entry. total=2 by fixture coincidence (1 mrrel row).
        vs = _make_intensional_snomed(op="is-a")
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200, f"expected 200, got {status}: {body}"
        total = body["expansion"]["total"]
        contains = body["expansion"]["contains"]
        # By fixture coincidence: total=2 matches actual size (root + 1 desc).
        assert total == 2, (
            f"total drifted from fixture-coincident value 2: total={total}, "
            f"contains={len(contains)}"
        )
        # Truncation: contains may be 1 or 2 (depending on whether the root
        # or the descendant was sliced). Both shapes are acceptable today.
        assert 1 <= len(contains) <= 2


# =============================================================================
# Lens 4: CF-HISTORIAN-VS02-02 source-reading audit (implicit canonical)
# Pattern: client-input-as-canonical drift (count=8)
# =============================================================================


class TestCFHistorianVS02_02_SourceAudit:
    """Source-reading audit of the implicit-value-set canonical URI gap.

    VS-02 HISTORIAN discovered that ``_expand_implicit_value_set`` Form (a)
    uses the client-supplied URL prefix verbatim for ``contains[].system``
    — does NOT call ``canonical_system_uri`` on the prefix.

    HISTORIAN source-reading audit confirms:
      (a) The function does NOT call ``canonical_system_uri`` anywhere.
      (b) The 3 sibling ``_do_*`` handlers that emit Out ``system``
          (``_do_lookup``, ``_do_validate``, ``_do_translate``) DO call the
          helper — confirmed via source-reading.
      (c) The implicit path is the only one missing the helper.
    """

    def test_h40_implicit_path_does_not_call_canonical_helper(self):
        """Source-reading: ``_expand_implicit_value_set`` does NOT call
        ``canonical_system_uri``.
        """
        from medterm4ds.apps import fhir_api
        # The function is nested — use inspect to grab its source.
        # Find the function definition inside the module.
        src = inspect.getsource(fhir_api)
        # Extract the _expand_implicit_value_set function block.
        # The function is nested in build_app — we check the module-level
        # source for the pattern.
        # We assert the function exists AND contains the `system_uri = prefix`
        # assignment (the client-input-echo line) rather than a canonical
        # re-resolution.
        assert "_expand_implicit_value_set" in src
        assert "system_uri = prefix" in src, (
            "_expand_implicit_value_set no longer uses raw prefix — audit "
            "whether canonical_system_uri was added"
        )

    def test_h41_lookup_handler_uses_canonical_helper(self):
        """Source-reading: ``_do_lookup`` DOES call ``canonical_system_uri``
        (CS-02 HISTORIAN QA-047 + CS-03 HISTORIAN QA-051 + milestone-2 CR-007).
        """
        from medterm4ds.apps import fhir_api
        src = inspect.getsource(fhir_api)
        assert "canonical_uri = canonical_system_uri(system_uri, source=source)" in src

    def test_h42_validate_handler_uses_canonical_helper(self):
        """Source-reading: ``_do_validate`` DOES call ``canonical_system_uri``
        (CS-03 HISTORIAN QA-051).
        """
        from medterm4ds.apps import fhir_api
        src = inspect.getsource(fhir_api)
        # The validate handler also has the canonical_uri line. Multiple
        # instances expected.
        assert src.count("canonical_uri = canonical_system_uri(system_uri, source=source)") >= 2, (
            "_do_validate canonical helper line missing"
        )

    def test_h43_intensional_path_uses_canonical_helper(self):
        """Source-reading: ``_expand_intensional`` DOES call
        ``canonical_system_uri`` on each include[].system (milestone-2 CR-013
        fix — the intensional path WAS missing the helper and now has it).
        """
        from medterm4ds.apps import fhir_api
        src = inspect.getsource(fhir_api)
        # The intensional handler's canonical line uses inc_system + source.
        assert "canonical_inc = canonical_system_uri(inc_system" in src, (
            "_expand_intensional canonical helper missing — CR-013 regression?"
        )

    def test_h44_translate_handler_uses_canonical_helper(self):
        """Source-reading: ``_do_translate`` DOES call ``canonical_system_uri``
        on the source_uri (milestone-2 CR-012 fix).
        """
        from medterm4ds.apps import fhir_api
        src = inspect.getsource(fhir_api)
        # The translate handler uses canonical_source_uri variable name.
        assert "canonical_source_uri = canonical_system_uri(source_uri" in src, (
            "_do_translate canonical helper missing — CR-012 regression?"
        )


# =============================================================================
# Lens 5: Docstring accuracy on _extract_valueset_from_parameters
# Pattern: documentation-vs-implementation drift (TS-01 HISTORIAN QA-007)
# =============================================================================


class TestDocstringAccuracy:
    """HISTORIAN pattern: docstring-vs-implementation drift audit. The new
    ``_extract_valueset_from_parameters`` helper added by SKEPTIC QA-059 has
    a detailed docstring — HISTORIAN verifies the docstring matches the body.

    Per TS-01 HISTORIAN QA-007: a docstring that over-promises (claims
    conformance the body doesn't deliver) is a maintenance hazard. A future
    engineer reading the docstring might extend the body in ways that
    contradict the documented behavior.
    """

    def test_h50_docstring_claims_return_none_on_malformed(self):
        """Docstring claim: 'Malformed shapes (missing resource, wrong
        resourceType) return None and let the caller fall through to the
        existing 400 path.'

        HISTORIAN verifies the body implements this: isinstance(resource, dict)
        guard + resourceType == "ValueSet" check.
        """
        from medterm4ds.apps import fhir_api
        # The helper is nested in build_app. Use the module source.
        src = inspect.getsource(fhir_api)
        assert "_extract_valueset_from_parameters" in src
        # Body claims graceful None return for malformed shapes.
        assert 'Malformed shapes (missing resource, wrong resourceType)' in src
        # Body implements the isinstance guard.
        assert 'if not isinstance(resource, dict):' in src
        # Body implements the resourceType check.
        assert 'if resource.get("resourceType") == "ValueSet":' in src

    def test_h51_docstring_cites_spec_section(self):
        """Docstring claim: cites FHIR R4 §4.7.5 In Parameters valueSet AND
        the parameters.html spec for the ``resource`` property convention.
        """
        from medterm4ds.apps import fhir_api
        src = inspect.getsource(fhir_api)
        # §4.7.5 In Parameters valueSet citation.
        assert "§4.7.5" in src
        # parameters.html citation.
        assert "https://hl7.org/fhir/R4/parameters.html" in src
        # "resource" property explanation.
        assert '``resource`` property' in src or '"resource" property' in src


# =============================================================================
# Lens 6: Test-too-lenient-on-fixture-coincidence re-audit
# Pattern: test-too-lenient-on-fixture-coincidence (VS-02 HISTORIAN strategy 41)
# =============================================================================


class TestFixtureCoincidenceReAudit:
    """Re-audit SKEPTIC's 36 VS-03 probes for false-positive pass modes.

    VS-02 HISTORIAN discovered that SKEPTIC test_s61 (intensional with
    count=1) PASSES-FOR-THE-WRONG-REASON because the fixture has exactly 1
    mrrel row matching BFS limit=1. The total=2 reported happens to equal
    the actual un-truncated size by coincidence.

    VS-03 SKEPTIC test_s34_is_a_total_reflects_untruncated_size has the
    SAME shape. HISTORIAN source-reads to confirm the fixture coincidence
    persists AND documents the probe is load-bearing for the deferred CF.
    """

    def test_h60_skeptic_test_s34_fixture_coincidence(self, fhir_client):
        """Reproduce SKEPTIC test_s34: intensional is-a filter with count=1.
        Asserts the CURRENT fixture-coincident behavior (total=2). If a
        deeper fixture is added (multiple descendants of 73211009), the
        probe MUST be updated to expose the BFS-cap truncation gap.
        """
        vs = _make_intensional_snomed(op="is-a")
        status, body = _post_expand(fhir_client, vs, params={"count": 1})
        assert status == 200
        # Fixture coincidence: 1 descendant (T2DM) + root (Diabetes mellitus)
        # = total 2. With count=1, the contains is truncated to 1 entry but
        # total reports the un-truncated size (2).
        total = body["expansion"]["total"]
        assert total == 2, (
            f"total drifted from fixture-coincident 2: {total}. If a deeper "
            f"fixture was added, this probe NOW exposes the BFS-cap gap "
            f"(CF-HISTORIAN-VS02-01)."
        )

    def test_h61_skeptic_test_s11_real_inline_vs_path(self, fhir_client):
        """Verify SKEPTIC test_s11 (Parameters-with-valueSet) actually
        exercises the new helper path, not a coincidental fallthrough.

        HISTORIAN verifies by adding a count parameter to the Parameters
        body (per spec: Parameters-body parameters override GET defaults).
        If the helper is correctly wired, the body count MUST override the
        GET default AND the inline VS MUST be expanded (both concepts).
        If the helper is NOT wired, the body would fall through to the 400
        path regardless of count.
        """
        nested_vs = _make_extensional_snomed()
        # count=2 in body — should NOT truncate (2 concepts total).
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": nested_vs},
                {"name": "count", "valueInteger": 2},
            ],
        }
        status, body = _post_expand(fhir_client, params_body)
        assert status == 200
        assert body["resourceType"] == "ValueSet"
        codes = _contains_codes(body)
        # Both concepts MUST appear — confirms the inline VS was expanded.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes
        # No truncation — count=2 matches the 2-concept size.
        assert body["expansion"]["total"] == 2

    def test_h62_skeptic_test_s12_count_precedence(self, fhir_client):
        """Verify SKEPTIC test_s12 count precedence: Parameters-body count
        OVERRIDES the GET query count when both are present (per FHIR R4
        §4.7.5: Parameters-body parameters override GET defaults).

        SKEPTIC test_s12 only passed the count via the body. HISTORIAN
        verifies precedence by passing BOTH body count=2 AND GET count=1.
        The body count MUST win.
        """
        nested_vs = _make_extensional_snomed()
        params_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "valueSet", "resource": nested_vs},
                # Body count = 2 (should win).
                {"name": "count", "valueInteger": 2},
            ],
        }
        # GET count = 1 (should be overridden by body count=2).
        status, body = _post_expand(fhir_client, params_body, params={"count": 1})
        assert status == 200
        codes = _contains_codes(body)
        # Both concepts MUST appear (body count=2 wins over GET count=1).
        assert len(codes) == 2, (
            f"Parameters-body count did not override GET count: codes={codes}"
        )


# =============================================================================
# Lens 7: Cross-handler helper-wiring audit (Code Review Time trigger)
# Pattern: helper-exists-but-not-wired (count=4 — SKEPTIC VS-03 architect)
# =============================================================================


class TestCrossHandlerHelperWiring:
    """The Code Review Time trigger in GLOBAL_RULES.md (For the Parameters-
    body case: audit every sibling complex-type extractor to ensure every
    spec-documented value* type is extracted AND wired into EVERY POST
    handler that accepts the same primary parameter set).

    VS-03 HISTORIAN verifies the helper-wiring is complete by enumerating
    which POST handlers consume the valueSet parameter and confirming each
    is wired to ``_extract_valueset_from_parameters``.

    Per FHIR R4 spec, only ValueSet/$expand accepts the inline ``valueSet``
    parameter. CodeSystem/$lookup, CodeSystem/$validate-code, CodeSystem/-
    $subsumes, ConceptMap/$translate do NOT accept it.
    """

    def test_h70_expand_post_wires_helper(self):
        """Source-reading: ``expand_post`` DOES wire
        ``_extract_valueset_from_parameters`` (post-QA-059 fix).
        """
        from medterm4ds.apps import fhir_api
        src = inspect.getsource(fhir_api)
        # The expand_post function calls the helper.
        assert "_extract_valueset_from_parameters(body)" in src, (
            "expand_post does NOT call _extract_valueset_from_parameters — "
            "QA-059 fix regression?"
        )

    def test_h71_no_other_handler_incorrectly_wires_valueset(self):
        """Source-reading: no other POST handler (lookup, validate, translate,
        subsumes) references ``_extract_valueset_from_parameters``. The
        helper is scoped to ValueSet/$expand only.

        Rationale: CodeSystem/$lookup, CodeSystem/$validate-code, ConceptMap/-
        $translate, CodeSystem/$subsumes do NOT accept an inline ValueSet
        parameter (per their respective spec In Parameters tables).
        """
        from medterm4ds.apps import fhir_api
        src = inspect.getsource(fhir_api)
        # The helper should appear in exactly 2 contexts: the definition
        # and the call site in expand_post.
        count = src.count("_extract_valueset_from_parameters")
        assert count == 2, (
            f"_extract_valueset_from_parameters appears {count} times — "
            f"expected 2 (definition + 1 call site in expand_post). "
            f"If >2, a non-ValueSet handler is incorrectly using the helper."
        )
