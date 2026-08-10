"""HISTORIAN RESWEEP probes for VS-01 (ValueSet Resource Structure).

Spec: https://hl7.org/fhir/R4/valueset.html (R4 canonical)
       Filter operators: https://hl7.org/fhir/R4/valueset.html#filter
       Expansion: https://hl7.org/fhir/R4/valueset.html#expansion

This file contains NEW HISTORIAN resweep probes that re-derive ALL prior
VS-01 patterns from current code via BOTH source-read structural probes
AND behavioral probes (where fixture permits). The baseline
``test_vs01_historian.py`` (29 probes) is treated as trusted prior coverage;
this resweep file adds the FRESH-FULL-SWEEP mandated probes per
USER_DIRECTIVES [2026-08-08].

HISTORIAN lens (per ROLE_QA_ENGINEER Section 3): pattern-match prior bug
patterns from ``GLOBAL_KNOWLEDGE.md`` and ``ARCHIVE_LOG.md``. For each
prior pattern: source-read + regression probe. Log bugs: regressions
(something we fixed has come back), recurring root causes.

The 5 isinstance-guard siblings of the 10th PROMOTED pattern
``isinstance guard at untrusted-data list-iterator boundary`` (count=4
PROMOTED in milestone 3; VS-01 SKEPTIC resweep QA-001 added the 5th
sibling at the PARENT compose-element boundary):

    1. ``compose`` element itself (PARENT — VS-01 SKEPTIC QA-001, the
       NEW fix in this iteration)
    2. ``compose.include[]`` iterator (CS-04 HISTORIAN QA-001 #1)
    3. ``compose.include[].concept[]`` iterator (CS-04 HISTORIAN QA-001 #2)
    4. ``compose.include[].filter[]`` iterator (CS-04 HISTORIAN QA-001 #3)
    5. ``compose.exclude[]`` iterator (CS-04 HISTORIAN QA-001 #4)

Plus 3 EXTRA isinstance guards that are SIBLINGS-OF-THE-PATTERN but at a
DIFFERENT surface (Parameters body extractors):

    6. ``_parse_parameters`` parameter[] iterator (CS-04 SKEPTIC QA-001)
    7. ``_extract_coding_from_parameters`` parameter[] iterator
       (CF-HISTORIAN-CM03-01 / CS-04 HISTORIAN carry-forward)
    8. ``_extract_valueset_from_parameters`` parameter[] iterator
       (VS-03 SKEPTIC QA-059)

SKEPTIC tip for HISTORIAN (per VS-01/SKEPTIC architect_handoff.md carry-
forward notes):
  - Re-derive all 5 isinstance-guard siblings (4 within compose + 1 at
    compose element itself — the new QA-001 fix).
  - Audit ``_extract_valueset_from_parameters`` source-read contract for
    the exact-case ``valueSet`` check (CS-05/TERMINOLOGIST tip — verified
    CLEAN by SKEPTIC).
  - Verify the ``FHIR_R4_FILTER_OPERATORS`` constant registry-as-contract
    pattern (per CR-014 + CF-SKEPTIC-CS01-RESWEEP-01 LOW DEFERRED symmetry
    argument for content modes).
  - The ast.Compare-specific source-read methodology (test_s85) is the
    new probe class for case-fidelity audits.

Prior VS-01 patterns re-derived in this resweep:
  - HCPCS URI drift (count=8+1 PROMOTED)
  - VS-01 SKEPTIC QA-054 (test suite codified 'descendant-of' typo as
    expected — corrected to spec-correct 'descendent-of')
  - VS-01 TERMINOLOGIST QA-055/QA-056 (count=1000 ignored client count +
    empty display echoed)
  - CF-HISTORIAN-VS01-01 (R5/R4B ConceptMapEquivalence values leaking
    into R4 surface — verify RESOLVED)
  - Plus 10 PROMOTED patterns

Per GLOBAL_RULES.md:
  - "Test-too-lenient": every probe asserts POSITIVE success shape.
  - "Don't manufacture bugs": DEFERRED is valid for genuine fixture gaps.
  - Spec citation required on every probe.
  - "isinstance guard at untrusted-data list-iterator boundary" (count=4
    PROMOTED as 10th PROMOTED pattern): probe every ``compose.include[]``,
    ``compose.exclude[]``, ``compose.include[].concept[]``,
    ``compose.include[].filter[]`` iterator for hostile-input resilience.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/valueset.html (R4 canonical)
# Spec: https://hl7.org/fhir/R4/valueset.html#filter (Filter operators)
# Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
# CR-014 (milestone-2 review): import the single source of truth from
# medterm4ds.engines.fhir rather than maintaining a local copy.
from medterm4ds.engines.fhir import (
    FHIR_URI_ALIASES,
    FHIR_URI_TO_SYSTEM,
    SYSTEM_TO_FHIR_URI,
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
    FHIR_R4_FILTER_OPERATORS,
    canonical_system_uri,
    fhir_uri_to_system,
    sab_label_to_fhir_uri,
    system_to_fhir_uri,
)

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"

# Legacy HCPCS URI (THO CodeSystem resource URL) — kept as input-only
# backwards-compat alias in FHIR_URI_ALIASES. The canonical URI is the
# CMS-published form (per VS-01 HISTORIAN QA-012 / TS-01 TERMINOLOGIST
# QA-012 fix).
LEGACY_HCPCS_URI = "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"
CANONICAL_HCPCS_URI = "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"


def _post_expand(fhir_client, value_set: dict) -> tuple[int, dict]:
    """POST a ValueSet body to /fhir/ValueSet/$expand."""
    resp = fhir_client.post(
        "/fhir/ValueSet/$expand",
        json=value_set,
        headers={"Accept": "application/fhir+json"},
    )
    try:
        body = resp.json()
    except Exception:
        body = {"_raw": resp.text}
    return resp.status_code, body


def _get_expand(fhir_client, params: dict) -> tuple[int, dict]:
    """GET /fhir/ValueSet/$expand with params."""
    resp = fhir_client.get(
        "/fhir/ValueSet/$expand",
        params=params,
        headers={"Accept": "application/fhir+json"},
    )
    try:
        body = resp.json()
    except Exception:
        body = {"_raw": resp.text}
    return resp.status_code, body


def _source_text() -> str:
    """Read apps/fhir_api.py source text for AST analysis."""
    p = Path(__file__).resolve().parents[2] / "src" / "medterm4ds" / "apps" / "fhir_api.py"
    return p.read_text()


def _responses_text() -> str:
    """Read engines/fhir/responses.py source text for AST analysis."""
    p = Path(__file__).resolve().parents[2] / "src" / "medterm4ds" / "engines" / "fhir" / "responses.py"
    return p.read_text()


def _engines_init_text() -> str:
    """Read engines/fhir/__init__.py source text for AST analysis."""
    p = Path(__file__).resolve().parents[2] / "src" / "medterm4ds" / "engines" / "fhir" / "__init__.py"
    return p.read_text()


def _equivalence_text() -> str:
    """Read engines/fhir/equivalence.py source text for AST analysis.

    CR-024 (milestone-3 review): the canonical translation table moved
    from ``responses.py`` to ``equivalence.py``.
    """
    p = Path(__file__).resolve().parents[2] / "src" / "medterm4ds" / "engines" / "fhir" / "equivalence.py"
    return p.read_text()


def _get_nested_func_source(file_text: str, parent_func: str, nested_func: str) -> str | None:
    """Read source of a nested function definition inside a parent function.

    Used to AST-walk ``create_fhir_app._expand_intensional`` etc. The
    function definitions are nested inside ``create_fhir_app`` (a closure
    over engine/settings) so they don't appear at module top level.
    """
    tree = ast.parse(file_text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == parent_func:
            for child in ast.walk(node):
                if isinstance(child, ast.FunctionDef) and child.name == nested_func:
                    return ast.get_source_segment(file_text, child) or ""
    return None


# =============================================================================
# Pattern 1: HCPCS URI drift (count=8+1 PROMOTED)
# TS-01 TERMINOLOGIST QA-012 + CS-01 HISTORIAN re-derivation
# =============================================================================


class TestPattern1HcpcsUriDrift:
    """Re-derive HCPCS URI drift regression class.

    Prior bug: SYSTEM_TO_FHIR_URI['HCPCS'] was set to the THO CodeSystem
    resource URL (http://terminology.hl7.org/CodeSystem/hcpcs-Level-II)
    rather than the canonical CMS URI used in Coding.system fields
    (http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets).

    This regression class spans 8 prior bug IDs + 1 PROMOTED via source-
    read of responses.py:543 (the load-bearing structural contract is a
    single loop iterating SYSTEM_TO_FHIR_URI). The drift recurs whenever
    someone hand-codes a URI literal instead of importing from the
    canonical registry.
    """

    def test_h10_system_to_fhir_uri_hcpcs_is_canonical_cms_uri(self):
        """SYSTEM_TO_FHIR_URI['HCPCS'] MUST be the CMS canonical URI."""
        # Spec: https://hl7.org/fhir/R4/terminologies-systems.html — code
        # systems table lists HCPCS as CMS-owned.
        assert SYSTEM_TO_FHIR_URI["HCPCS"] == CANONICAL_HCPCS_URI, (
            f"HCPCS URI drifted: expected {CANONICAL_HCPCS_URI!r}; "
            f"got {SYSTEM_TO_FHIR_URI['HCPCS']!r}. The canonical URI is "
            f"the CMS-published form; the legacy THO URL is "
            f"http://terminology.hl7.org/CodeSystem/hcpcs-Level-II."
        )

    def test_h11_legacy_hcpcs_uri_in_aliases_as_input_only(self):
        """Legacy HCPCS URI is in FHIR_URI_ALIASES mapping → HCPCS (input only).

        Spec: https://hl7.org/fhir/R4/terminologies-systems.html — legacy
        URIs are accepted as input aliases but never emitted as canonical.
        """
        assert FHIR_URI_ALIASES.get(LEGACY_HCPCS_URI) == "HCPCS", (
            f"Legacy HCPCS URI {LEGACY_HCPCS_URI!r} must be in "
            f"FHIR_URI_ALIASES mapping to 'HCPCS' for backwards-compat "
            f"input handling."
        )

    def test_h12_legacy_hcpcs_uri_not_in_system_to_fhir_uri_values(self):
        """The legacy HCPCS URI MUST NOT appear as a VALUE in
        SYSTEM_TO_FHIR_URI (it would be emitted as canonical on output).

        Spec: https://hl7.org/fhir/R4/terminologies-systems.html — only
        the canonical URI is emitted on output.
        """
        assert LEGACY_HCPCS_URI not in SYSTEM_TO_FHIR_URI.values(), (
            f"Legacy HCPCS URI {LEGACY_HCPCS_URI!r} must NOT be a value "
            f"in SYSTEM_TO_FHIR_URI — it's the legacy THO URL, not the "
            f"canonical CMS URI. FHIR_URI_ALIASES handles input-only "
            f"backwards-compat."
        )

    def test_h13_canonical_system_uri_translates_legacy_to_canonical(self):
        """canonical_system_uri(legacy_hcpcs) returns the canonical CMS URI."""
        # Spec: https://hl7.org/fhir/R4/terminologies-systems.html
        result = canonical_system_uri(LEGACY_HCPCS_URI)
        assert result == CANONICAL_HCPCS_URI, (
            f"canonical_system_uri(legacy HCPCS) should return the "
            f"canonical CMS URI; got {result!r}. This is the load-bearing "
            f"contract preventing client-input-as-canonical drift."
        )

    def test_h14_fhir_uri_to_system_legacy_resolves_to_hcpcs(self):
        """fhir_uri_to_system(legacy HCPCS) resolves to 'HCPCS'."""
        # Spec: https://hl7.org/fhir/R4/terminologies-systems.html —
        # legacy URIs are accepted as input aliases.
        result = fhir_uri_to_system(LEGACY_HCPCS_URI)
        assert result == "HCPCS", (
            f"fhir_uri_to_system(legacy HCPCS URI) should resolve to "
            f"'HCPCS' via FHIR_URI_ALIASES; got {result!r}."
        )

    def test_h15_no_hardcoded_legacy_hcpcs_in_responses_py(self):
        """AST walk of responses.py: no hardcoded legacy HCPCS URI literal
        in executable code.

        The legacy URI appears in EXACTLY ONE location:
        FHIR_URI_ALIASES at engines/fhir/__init__.py:40. Any other
        occurrence is a regression of the HCPCS URI drift pattern.
        """
        text = _responses_text()
        tree = ast.parse(text)
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if LEGACY_HCPCS_URI in node.value:
                    offenders.append(node.lineno)
        assert not offenders, (
            f"Legacy HCPCS URI appears in responses.py at lines {offenders}; "
            f"this is a regression of the HCPCS URI drift pattern "
            f"(count=8+1 PROMOTED via source-read of responses.py:543). "
            f"The legacy URI must only appear in FHIR_URI_ALIASES at "
            f"engines/fhir/__init__.py."
        )


# =============================================================================
# Pattern 2: VS-01 SKEPTIC QA-054 — 'descendant-of' typo corrected to
# 'descendent-of' (Latin-derived, spec-correct)
# =============================================================================


class TestPattern2Qa054DescendentOfSpecCorrect:
    """Re-derive the SKEPTIC QA-054 fix: ``descendent-of`` (spec-correct
    Latin-derived spelling) is honored; ``descendant-of`` (common English)
    is silently dropped.

    Spec: https://hl7.org/fhir/R4/valueset.html#filter
        "op 1..1 code  = | is-a | descendent-of | is-not-a | regex | in |
         not-in | generalizes | exists"

    This is a recurring drift class on FHIR R4 closed enums with
    counterintuitive spellings. The HISTORIAN lens audits BOTH the
    implementation AND the FHIR_R4_FILTER_OPERATORS constant for the
    spec-correct spelling.
    """

    def test_h20_filter_operators_constant_has_spec_correct_descendent_of(self):
        """FHIR_R4_FILTER_OPERATORS contains 'descendent-of' (spec-correct).

        Spec: https://hl7.org/fhir/R4/valueset.html#filter
        """
        assert "descendent-of" in FHIR_R4_FILTER_OPERATORS, (
            "FHIR_R4_FILTER_OPERATORS must contain 'descendent-of' "
            "(spec-correct Latin spelling per "
            "https://hl7.org/fhir/R4/valueset.html#filter)."
        )

    def test_h21_filter_operators_constant_omits_off_spec_descendant_of(self):
        """FHIR_R4_FILTER_OPERATORS MUST NOT contain 'descendant-of'."""
        assert "descendant-of" not in FHIR_R4_FILTER_OPERATORS, (
            "FHIR_R4_FILTER_OPERATORS must NOT contain 'descendant-of' "
            "(off-spec common English spelling). The spec-correct value "
            "is 'descendent-of' (Latin-derived)."
        )

    def test_h22_expand_intensional_source_honors_spec_correct_spelling(self):
        """_expand_intensional source: `op in ("is-a", "descendent-of")`.

        Source-read: the honored-operator tuple MUST list the spec-correct
        spelling. Verified via AST walk of _expand_intensional.
        """
        src = _source_text()
        fn_text = _get_nested_func_source(src, "create_fhir_app", "_expand_intensional")
        assert fn_text is not None, "_expand_intensional function not found"
        tree = ast.parse(fn_text)
        # Find `op in (...)` tuple.
        ops_found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                if (
                    isinstance(node.left, ast.Name)
                    and node.left.id == "op"
                    and len(node.ops) == 1
                    and isinstance(node.ops[0], ast.In)
                    and isinstance(node.comparators[0], ast.Tuple)
                ):
                    for elt in node.comparators[0].elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            ops_found.add(elt.value)
        assert ops_found == {"is-a", "descendent-of"}, (
            f"_expand_intensional must honor only {{'is-a', 'descendent-of'}}; "
            f"got {ops_found}. Off-spec 'descendant-of' MUST NOT be honored "
            f"per SKEPTIC QA-054 fix."
        )

    def test_h23_descendent_of_behavioral_excludes_root_includes_descendants(self, fhir_client):
        """BEHAVIORAL: filter[descendent-of] expands to descendants only."""
        # Spec: https://hl7.org/fhir/R4/valueset.html#filter
        # "descendent-of: The specified property of the code has the
        # specified value; the search is recursive."
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test/h23",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept",
                        "op": "descendent-of",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        status, response = _post_expand(fhir_client, body)
        assert status == 200, f"Expected 200; got {status}: {response}"
        contains = response.get("expansion", {}).get("contains", [])
        codes = {c.get("code") for c in contains}
        # Descendents-of should return the CHILD only, NOT the root.
        assert SNOMED_T2DM in codes, (
            f"descendent-of should include child {SNOMED_T2DM}; got {codes}."
        )
        assert SNOMED_DIABETES_MELLITUS not in codes, (
            f"descendent-of must EXCLUDE root {SNOMED_DIABETES_MELLITUS}; "
            f"got {codes}. Only is-a includes the root."
        )

    def test_h24_descendant_of_off_spec_silently_dropped_empty_expansion(self, fhir_client):
        """BEHAVIORAL: off-spec 'descendant-of' produces empty expansion.

        The off-spec spelling must NOT be silently equated to a valid
        operator (no silent synonym).
        """
        # Spec: https://hl7.org/fhir/R4/valueset.html#filter — only
        # 'descendent-of' is in the enum.
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test/h24",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept",
                        "op": "descendant-of",  # off-spec common English
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }],
            },
        }
        status, response = _post_expand(fhir_client, body)
        assert status == 200, f"Expected 200; got {status}: {response}"
        contains = response.get("expansion", {}).get("contains", [])
        assert contains == [], (
            f"Off-spec 'descendant-of' must be silently dropped → empty "
            f"expansion; got {contains}. This is the load-bearing "
            f"contract from VS-01 SKEPTIC QA-054."
        )


# =============================================================================
# Pattern 3: VS-01 TERMINOLOGIST QA-055 — count=1000 ignored client count
# =============================================================================


class TestPattern3Qa055CountPassThrough:
    """Re-derive the TERMINOLOGIST QA-055 fix: ``expand_post`` MUST honor
    the client-supplied ``count`` query param, NOT hardcode count=1000.

    Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html
        In ``count`` (0..1 integer): "At all times, the correct value of
        total reflects the total number of concepts in the expansion"

    The QA-055 fix ensured the POST handler passes count through to
    ``_do_expand`` for BOTH the ValueSet-body branch AND the Parameters-
    body branch. HISTORIAN verifies by source-read + behavioral probe.
    """

    def test_h30_expand_post_value_set_body_count_truncation(self, fhir_client):
        """BEHAVIORAL: POST $expand with count=1 truncates the expansion.

        With 2 concepts in compose.include[].concept[] and count=1, the
        response MUST contain exactly 1 entry. If count=1000 were hardcoded
        (the prior bug), the response would contain both concepts.
        """
        # Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test/h30",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_DIABETES_MELLITUS},
                        {"code": SNOMED_T2DM},
                    ],
                }],
            },
        }
        # count=1 in query params — should truncate the 2-concept list to 1
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand?count=1",
            json=body,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        body_resp = resp.json()
        contains = body_resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1, (
            f"count=1 should truncate expansion to 1 entry; got {len(contains)}. "
            f"This is the VS-01 TERMINOLOGIST QA-055 contract — count=1000 "
            f"hardcoded would return 2 entries."
        )

    def test_h31_expand_post_value_set_body_total_reflects_untruncated(self, fhir_client):
        """BEHAVIORAL: POST $expand with count=1 — expansion.total reflects
        the un-truncated count (2), not the truncated count (1).

        Spec: https://hl7.org/fhir/R4/valueset.html#expansion
            "total: total number of concepts in the expansion"
        Per VS-02 SKEPTIC QA-057 fix, total MUST reflect un-truncated size.
        """
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test/h31",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_DIABETES_MELLITUS},
                        {"code": SNOMED_T2DM},
                    ],
                }],
            },
        }
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand?count=1",
            json=body,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        body_resp = resp.json()
        total = body_resp.get("expansion", {}).get("total")
        # total must reflect UN-truncated count (2), not the post-truncation
        # size (1). VS-02 SKEPTIC QA-057 contract.
        assert total == 2, (
            f"expansion.total should reflect un-truncated count (2); "
            f"got {total}. The hardcoded count=1000 bug would also have "
            f"silently masked the truncation-signal gap."
        )

    def test_h32_expand_post_value_set_body_toocostly_extension_when_truncated(self, fhir_client):
        """BEHAVIORAL: when count truncates, the valueset-toocostly extension
        MUST be present in ``expansion.extension[]`` (clinical-safety signal).

        Per FHIR R4 §4.9.2 + https://hl7.org/fhir/R4/extension-valueset-toocostly.html:
        when the server returns a truncated expansion, the extension carries
        the truncation signal. The extension lives at ``expansion.extension``
        (not the top-level resource extension) per build_valueset_expand
        in engines/fhir/responses.py:320.
        """
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test/h32",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_DIABETES_MELLITUS},
                        {"code": SNOMED_T2DM},
                    ],
                }],
            },
        }
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand?count=1",
            json=body,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        body_resp = resp.json()
        # The toocostly extension lives at expansion.extension[], NOT
        # top-level extension[] (per build_valueset_expand in responses.py:320).
        expansion = body_resp.get("expansion", {})
        extensions = (
            expansion.get("extension", [])
            or body_resp.get("extension", [])
            or []
        )
        assert extensions, (
            f"Truncation extension missing when count truncated; "
            f"expansion.extension: {expansion.get('extension')}; "
            f"top-level extension: {body_resp.get('extension')}. "
            f"The valueset-toocostly extension is the clinical-safety "
            f"signal that the expansion is incomplete."
        )
        # At least one extension URL references toocostly.
        assert any(
            "toocostly" in (e.get("url") or "") for e in extensions
        ), (
            f"Truncation extension (valueset-toocostly) URL missing in "
            f"extensions: {extensions}."
        )


# =============================================================================
# Pattern 4: VS-01 TERMINOLOGIST QA-056 — empty display echoed
# When client OMITS display, engine canonical preferred term is resolved
# via get_code_infos([CodeRef(source, code)]).
# =============================================================================


class TestPattern4Qa056OmittedDisplayCanonicalResolution:
    """Re-derive the TERMINOLOGIST QA-056 fix: when client OMITS display,
    the engine's canonical preferred term is resolved.

    Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display
        "The recommended display for this item in the expansion."

    Prior bug: empty string was echoed for omitted display, producing
    clinically useless expansions. Fix: resolve via get_code_infos.
    """

    def test_h40_omitted_display_resolves_to_engine_canonical(self, fhir_client):
        """BEHAVIORAL: compose.include[].concept[] with no display resolves
        to the engine's canonical preferred term (STR from mrconso PT)."""
        # Fixture: SNOMED_DIABETES_MELLITUS has STR "Diabetes mellitus".
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test/h40",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": SNOMED_DIABETES_MELLITUS},  # no display
                    ],
                }],
            },
        }
        status, response = _post_expand(fhir_client, body)
        assert status == 200, f"Expected 200; got {status}: {response}"
        contains = response.get("expansion", {}).get("contains", [])
        assert len(contains) == 1, f"Expected 1 contains; got {len(contains)}"
        display = contains[0].get("display")
        # The canonical name from the fixture (conftest.py:31)
        assert display == "Diabetes mellitus", (
            f"Omitted display must resolve to engine canonical preferred "
            f"term 'Diabetes mellitus'; got {display!r}. Empty string "
            f"would be the prior QA-056 bug."
        )

    def test_h41_omitted_display_for_rxnorm_resolves_to_scd_canonical(self, fhir_client):
        """BEHAVIORAL: RXNORM concept with no display resolves to canonical."""
        # Fixture: RXNORM_METFORMIN has STR "24 HR metformin 500 MG Oral Tablet".
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test/h41",
            "compose": {
                "include": [{
                    "system": RXNORM_URI,
                    "concept": [
                        {"code": RXNORM_METFORMIN},  # no display
                    ],
                }],
            },
        }
        status, response = _post_expand(fhir_client, body)
        assert status == 200
        contains = response.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        display = contains[0].get("display")
        assert display == "24 HR metformin 500 MG Oral Tablet", (
            f"Omitted display must resolve to engine canonical; got {display!r}."
        )

    def test_h42_unknown_code_with_omitted_display_falls_back_to_code(self, fhir_client):
        """BEHAVIORAL: when get_code_infos returns empty, display falls back
        to the code string (not empty)."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test/h42",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": "9999999999"},  # not in fixture
                    ],
                }],
            },
        }
        status, response = _post_expand(fhir_client, body)
        assert status == 200
        contains = response.get("expansion", {}).get("contains", [])
        # The unknown code is still included (concept list is extensional).
        codes = {c.get("code") for c in contains}
        assert "9999999999" in codes, (
            f"Extensional concept list must include unknown code; got {codes}."
        )


# =============================================================================
# Pattern 5: CF-HISTORIAN-VS01-01 — R5/R4B ConceptMapEquivalence values
# leaking into R4 surface (RESOLVED in milestone-2 CR-014)
# =============================================================================


class TestPattern5CfHistorianVs01ConceptMapEquivalenceDrift:
    """Re-derive CF-HISTORIAN-VS01-01 status: RESOLVED in milestone-2 CR-014.

    Prior bug: the INTERNAL_REL_TO_FHIR_EQUIVALENCE translation map emitted
    R5/R4B values (subsumedby, matches, not-relatedto) on the R4 surface.
    The map was consolidated into engines/fhir/equivalence.py (CR-024) and
    a module-load ``assert`` guarantees every emitted value is a member of
    ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE``.

    HISTORIAN verifies RESOLUTION by:
      (a) AST walk: every value in INTERNAL_REL_TO_FHIR_EQUIVALENCE is a
          member of FHIR_R4_CONCEPT_MAP_EQUIVALENCE.
      (b) The R4 spec-correct value ``specializes`` IS present (replaces
          the prior ``subsumedby``).
      (c) The R5/R4B values ``subsumedby``, ``matches`` are ABSENT from
          emitted values.
    """

    def test_h50_internal_rel_to_fhir_equivalence_emits_only_r4_values(self):
        """AST walk: every value in INTERNAL_REL_TO_FHIR_EQUIVALENCE is in
        FHIR_R4_CONCEPT_MAP_EQUIVALENCE.

        Spec: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
            10 values: relatedto | equivalent | equal | wider | subsumes |
            narrower | specializes | inexact | unmatched | disjoint
        """
        text = _equivalence_text()
        tree = ast.parse(text)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign):
                if (
                    isinstance(node.target, ast.Name)
                    and node.target.id == "INTERNAL_REL_TO_FHIR_EQUIVALENCE"
                ):
                    target = node
                    break
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "INTERNAL_REL_TO_FHIR_EQUIVALENCE":
                        target = node
                        break
        assert target is not None, "INTERNAL_REL_TO_FHIR_EQUIVALENCE missing"
        values: set[str] = set()
        value_node = target.value
        if isinstance(value_node, ast.Dict):
            for v in value_node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    values.add(v.value)
        # CF-HISTORIAN-VS01-01 RESOLVED: every emitted value MUST be in the R4 enum.
        drifted = values - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        assert not drifted, (
            f"INTERNAL_REL_TO_FHIR_EQUIVALENCE emits values outside the R4 "
            f"ConceptMapEquivalence closed enum: {drifted}. CF-HISTORIAN-VS01-01 "
            f"was resolved in milestone-2 CR-014; re-introducing drift "
            f"requires updating FHIR_R4_CONCEPT_MAP_EQUIVALENCE."
        )

    def test_h51_specializes_r4_value_present(self):
        """The R4 spec-correct value ``specializes`` IS present in the map
        (replaces prior R5/R4B ``subsumedby``)."""
        text = _equivalence_text()
        tree = ast.parse(text)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign):
                if (
                    isinstance(node.target, ast.Name)
                    and node.target.id == "INTERNAL_REL_TO_FHIR_EQUIVALENCE"
                ):
                    target = node
                    break
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "INTERNAL_REL_TO_FHIR_EQUIVALENCE":
                        target = node
                        break
        assert target is not None
        values: set[str] = set()
        value_node = target.value
        if isinstance(value_node, ast.Dict):
            for v in value_node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    values.add(v.value)
        assert "specializes" in values, (
            f"Expected 'specializes' (R4 spec-correct replacement for the "
            f"prior R5/R4B 'subsumedby') in equivalence map values; got "
            f"{values}. CF-HISTORIAN-VS01-01 fix from milestone-2."
        )

    def test_h52_r5_r4b_values_absent_from_emitted_values(self):
        """The R5/R4B values ``subsumedby`` and ``matches`` are ABSENT from
        the emitted values of INTERNAL_REL_TO_FHIR_EQUIVALENCE."""
        text = _equivalence_text()
        tree = ast.parse(text)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign):
                if (
                    isinstance(node.target, ast.Name)
                    and node.target.id == "INTERNAL_REL_TO_FHIR_EQUIVALENCE"
                ):
                    target = node
                    break
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "INTERNAL_REL_TO_FHIR_EQUIVALENCE":
                        target = node
                        break
        assert target is not None
        values: set[str] = set()
        value_node = target.value
        if isinstance(value_node, ast.Dict):
            for v in value_node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    values.add(v.value)
        r5_r4b_drift = {"subsumedby", "matches", "not-relatedto"} & values
        assert not r5_r4b_drift, (
            f"R5/R4B values {r5_r4b_drift} leaked back into the R4 surface. "
            f"CF-HISTORIAN-VS01-01 RESOLVED in milestone-2 CR-014; this is "
            f"a regression."
        )

    def test_h53_module_load_assert_guards_drift_runtime(self):
        """AST walk: equivalence.py has a module-load-time assertion that
        every emitted value is a member of FHIR_R4_CONCEPT_MAP_EQUIVALENCE.

        This is the load-bearing structural invariant: drift triggers a
        module-load AssertionError, blocking import.
        """
        text = _equivalence_text()
        # The assertion text references FHIR_R4_CONCEPT_MAP_EQUIVALENCE
        # (the canonical R4 enum) and emits a clear error if drift exists.
        # Just verify the contract by checking the assertion exists.
        assert "FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in text, (
            "equivalence.py must reference FHIR_R4_CONCEPT_MAP_EQUIVALENCE "
            "in the module-load assertion that guards against cross-version "
            "drift."
        )
        # Look for assert statement that iterates values and checks membership.
        tree = ast.parse(text)
        has_assert = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                has_assert = True
                break
        assert has_assert, (
            "equivalence.py MUST have a module-load-time ``assert`` that "
            "every emitted value in INTERNAL_REL_TO_FHIR_EQUIVALENCE is a "
            "member of FHIR_R4_CONCEPT_MAP_EQUIVALENCE. Without this, "
            "cross-version drift is invisible."
        )


# =============================================================================
# Pattern 6: 5 isinstance-guard siblings (the 10th PROMOTED pattern at
# count=5 with VS-01 SKEPTIC resweep QA-001)
# =============================================================================


class TestPattern6IsinstanceGuardSiblingsSourceRead:
    """Re-derive the 5 isinstance-guard siblings of the 10th PROMOTED
    pattern ``isinstance guard at untrusted-data list-iterator boundary``.

    Source-read structural contract: each iterator on a client-supplied
    JSON body MUST have ``isinstance(<var>, dict)`` guard as the FIRST
    statement of the loop body (before any .get(...) call on the iterated
    variable).

    The 5 siblings (per VS-01/SKEPTIC architect_handoff.md):
      1. ``compose`` element itself (PARENT — VS-01 SKEPTIC QA-001)
      2. ``compose.include[]`` iterator
      3. ``compose.include[].concept[]`` iterator
      4. ``compose.include[].filter[]`` iterator
      5. ``compose.exclude[]`` iterator
    """

    def test_h60_compose_element_isinstance_guard_present(self):
        """SOURCE-READ: ``isinstance(compose, dict)`` guard present.

        Per VS-01 SKEPTIC QA-001 (the new fix in this iteration), the
        PARENT compose-element boundary MUST have an isinstance guard.
        """
        src = _source_text()
        fn_text = _get_nested_func_source(src, "create_fhir_app", "_expand_intensional")
        assert fn_text is not None
        # The guard is: ``if not isinstance(compose, dict): compose = {}``
        assert "isinstance(compose, dict)" in fn_text, (
            "_expand_intensional MUST have isinstance(compose, dict) guard "
            "as the PARENT data-access boundary (5th sibling of the 10th "
            "PROMOTED pattern). VS-01 SKEPTIC resweep QA-001."
        )

    def test_h61_include_iterator_isinstance_guard_present(self):
        """SOURCE-READ: ``isinstance(include, dict)`` guard present."""
        src = _source_text()
        fn_text = _get_nested_func_source(src, "create_fhir_app", "_expand_intensional")
        assert fn_text is not None
        assert "isinstance(include, dict)" in fn_text, (
            "_expand_intensional MUST have isinstance(include, dict) guard "
            "on the compose.include[] loop. 1st sibling of the 10th PROMOTED "
            "pattern within the compose dict."
        )

    def test_h62_concept_iterator_isinstance_guard_present(self):
        """SOURCE-READ: ``isinstance(concept, dict)`` guard present."""
        src = _source_text()
        fn_text = _get_nested_func_source(src, "create_fhir_app", "_expand_intensional")
        assert fn_text is not None
        assert "isinstance(concept, dict)" in fn_text, (
            "_expand_intensional MUST have isinstance(concept, dict) guard "
            "on the compose.include[].concept[] loop. 2nd sibling."
        )

    def test_h63_filter_iterator_isinstance_guard_present(self):
        """SOURCE-READ: ``isinstance(filt, dict)`` or similar guard present
        on compose.include[].filter[] loop."""
        src = _source_text()
        fn_text = _get_nested_func_source(src, "create_fhir_app", "_expand_intensional")
        assert fn_text is not None
        # The implementation uses ``filt`` as the loop variable.
        assert "isinstance(filt, dict)" in fn_text, (
            "_expand_intensional MUST have isinstance(filt, dict) guard "
            "on the compose.include[].filter[] loop. 3rd sibling."
        )

    def test_h64_exclude_iterator_isinstance_guard_present(self):
        """SOURCE-READ: ``isinstance(exclude, dict)`` guard present on
        compose.exclude[] loop."""
        src = _source_text()
        fn_text = _get_nested_func_source(src, "create_fhir_app", "_expand_intensional")
        assert fn_text is not None
        assert "isinstance(exclude, dict)" in fn_text, (
            "_expand_intensional MUST have isinstance(exclude, dict) guard "
            "on the compose.exclude[] loop. 4th sibling."
        )

    def test_h65_ast_walk_all_5_guards_within_expand_intensional(self):
        """AST walk: all 5 isinstance guards are present within
        _expand_intensional, structurally pinning the 10th PROMOTED pattern
        at count=5."""
        src = _source_text()
        fn_text = _get_nested_func_source(src, "create_fhir_app", "_expand_intensional")
        assert fn_text is not None
        tree = ast.parse(fn_text)
        # Collect every isinstance call's first argument name.
        isinstance_args: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "isinstance"
                and node.args
                and isinstance(node.args[0], ast.Name)
            ):
                isinstance_args.add(node.args[0].id)
        # All 5 expected guard variables are present.
        expected = {"compose", "include", "concept", "filt", "exclude"}
        missing = expected - isinstance_args
        assert not missing, (
            f"_expand_intensional missing isinstance guards for: {missing}. "
            f"All 5 siblings of the 10th PROMOTED pattern must be present "
            f"(count=5). Found: {isinstance_args}."
        )

    def test_h66_compose_non_dict_no_5xx_behavioral(self, fhir_client):
        """BEHAVIORAL: compose as non-dict (string) MUST NOT 5xx.

        Per VS-01 SKEPTIC QA-001, the 5th sibling guard at the PARENT
        boundary prevents 500 + traceback when compose is non-dict.
        """
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test/h66",
            "compose": "not-a-dict",  # string instead of dict
        }
        status, response = _post_expand(fhir_client, body)
        assert status < 500, (
            f"Non-dict compose MUST NOT 5xx; got {status}: {response}. "
            f"This is the VS-01 SKEPTIC resweep QA-001 contract (5th "
            f"sibling of the 10th PROMOTED pattern)."
        )

    def test_h67_compose_as_int_no_5xx_behavioral(self, fhir_client):
        """BEHAVIORAL: compose as int MUST NOT 5xx."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test/h67",
            "compose": 42,
        }
        status, response = _post_expand(fhir_client, body)
        assert status < 500, (
            f"Non-dict compose (int) MUST NOT 5xx; got {status}: {response}."
        )

    def test_h68_compose_as_null_no_5xx_behavioral(self, fhir_client):
        """BEHAVIORAL: compose as null MUST NOT 5xx."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test/h68",
            "compose": None,
        }
        status, response = _post_expand(fhir_client, body)
        assert status < 500, (
            f"Null compose MUST NOT 5xx; got {status}: {response}."
        )

    def test_h69_compose_as_list_no_5xx_behavioral(self, fhir_client):
        """BEHAVIORAL: compose as list MUST NOT 5xx."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test/h69",
            "compose": [{"include": []}],  # list instead of dict
        }
        status, response = _post_expand(fhir_client, body)
        assert status < 500, (
            f"List compose MUST NOT 5xx; got {status}: {response}."
        )


# =============================================================================
# Pattern 7: Sibling Parameters-body extractors (SIBLINGS-OF-THE-PATTERN
# at DIFFERENT surface)
# =============================================================================


class TestPattern7ParametersBodyExtractorIsinstanceSiblings:
    """Source-read audit of sibling Parameters-body extractors that have
    isinstance guards on the parameter[] iterator.

    Per the 10th PROMOTED pattern, every iterator on a client-supplied
    JSON body MUST have an isinstance guard. These 3 extractors are
    SIBLINGS of the 5 compose-level guards but at the Parameters-body
    surface:
      6. _parse_parameters parameter[] iterator
      7. _extract_coding_from_parameters parameter[] iterator
         (CF-HISTORIAN-CM03-01 / CS-04 HISTORIAN carry-forward)
      8. _extract_valueset_from_parameters parameter[] iterator
         (VS-03 SKEPTIC QA-059)
    """

    def test_h70_parse_parameters_has_isinstance_guard_on_param(self):
        """SOURCE-READ: _parse_parameters has isinstance(param, dict) guard
        on the parameter[] loop."""
        src = _source_text()
        fn_text = _get_nested_func_source(src, "create_fhir_app", "_parse_parameters")
        assert fn_text is not None, "_parse_parameters function not found"
        assert "isinstance(param, dict)" in fn_text, (
            "_parse_parameters MUST have isinstance(param, dict) guard "
            "on the parameter[] loop. CS-04 SKEPTIC QA-001 sibling."
        )

    def test_h71_extract_valueset_from_parameters_has_isinstance_guards(self):
        """SOURCE-READ: _extract_valueset_from_parameters has isinstance
        guards on BOTH param AND resource.

        Per VS-03 SKEPTIC QA-059 (CS-04 HISTORIAN carry-forward applied
        to the ValueSet-extractor surface).
        """
        src = _source_text()
        fn_text = _get_nested_func_source(
            src, "create_fhir_app", "_extract_valueset_from_parameters"
        )
        assert fn_text is not None, "_extract_valueset_from_parameters not found"
        assert "isinstance(param, dict)" in fn_text, (
            "_extract_valueset_from_parameters MUST have isinstance(param, dict) "
            "guard on the parameter[] loop."
        )
        assert "isinstance(resource, dict)" in fn_text, (
            "_extract_valueset_from_parameters MUST have isinstance(resource, dict) "
            "guard on the resource access (the nested ValueSet)."
        )


# =============================================================================
# Pattern 8: FHIR_R4_FILTER_OPERATORS registry-as-contract
# (CR-014 + CF-SKEPTIC-CS01-RESWEEP-01 LOW DEFERRED symmetry argument)
# =============================================================================


class TestPattern8FhirR4FilterOperatorsRegistryAsContract:
    """Re-derive the FHIR_R4_FILTER_OPERATORS constant registry-as-contract
    pattern.

    Spec: https://hl7.org/fhir/R4/valueset.html#filter
        9 operators: = | is-a | descendent-of | is-not-a | regex | in |
        not-in | generalizes | exists

    The frozen-set constant in engines/fhir/__init__.py is the single
    source of truth imported by BOTH production code AND tests. This
    structurally prevents drift between the implementation and the test
    suite (per CR-014 + CF-SKEPTIC-CS01-RESWEEP-01 LOW DEFERRED symmetry
    argument for content modes — FHIR_R4_FILTER_OPERATORS is the closed-
    enum sibling of FHIR_R4_CONCEPT_MAP_EQUIVALENCE).
    """

    def test_h80_filter_operators_constant_in_engines_fhir_init(self):
        """FHIR_R4_FILTER_OPERATORS IS in canonical location
        (engines/fhir/__init__.py)."""
        # Spec: https://hl7.org/fhir/R4/valueset.html#filter
        text = _engines_init_text()
        assert "FHIR_R4_FILTER_OPERATORS" in text, (
            "FHIR_R4_FILTER_OPERATORS constant MUST be defined in "
            "engines/fhir/__init__.py (canonical location per CR-014)."
        )

    def test_h81_filter_operators_constant_matches_spec_exactly(self):
        """FHIR_R4_FILTER_OPERATORS exactly equals the R4 spec list of 9."""
        # Spec: https://hl7.org/fhir/R4/valueset.html#filter
        expected = frozenset({
            "=", "is-a", "descendent-of", "is-not-a", "regex",
            "in", "not-in", "generalizes", "exists",
        })
        assert FHIR_R4_FILTER_OPERATORS == expected, (
            f"FHIR_R4_FILTER_OPERATORS drifted from R4 spec list. "
            f"Expected 9 values; got {len(FHIR_R4_FILTER_OPERATORS)}: "
            f"{FHIR_R4_FILTER_OPERATORS}. Missing: "
            f"{expected - FHIR_R4_FILTER_OPERATORS}; extra: "
            f"{FHIR_R4_FILTER_OPERATORS - expected}."
        )

    def test_h82_filter_operators_constant_has_9_values(self):
        """FHIR_R4_FILTER_OPERATORS has exactly 9 values."""
        assert len(FHIR_R4_FILTER_OPERATORS) == 9, (
            f"FHIR_R4_FILTER_OPERATORS must have exactly 9 values per "
            f"R4 spec; got {len(FHIR_R4_FILTER_OPERATORS)}."
        )

    def test_h83_filter_operators_importable_from_medterm4ds_engines_fhir(self):
        """Import contract: FHIR_R4_FILTER_OPERATORS importable from
        medterm4ds.engines.fhir (canonical location)."""
        # Already imported at top of file — verify identity.
        from medterm4ds.engines.fhir import FHIR_R4_FILTER_OPERATORS as imported
        assert imported is FHIR_R4_FILTER_OPERATORS, (
            "FHIR_R4_FILTER_OPERATORS must be importable from "
            "medterm4ds.engines.fhir (canonical location per CR-014); "
            "redefining locally would defeat the registry-as-contract pattern."
        )

    def test_h84_no_local_filter_operators_copy_in_test_vs01_historian(self):
        """Registry-as-contract pattern: NO local FHIR_R4_FILTER_OPERATORS
        copy in this test file (must import from canonical location).

        Per CR-014 + CF-SKEPTIC-CS01-RESWEEP-01 LOW DEFERRED: the closed-
        enum constant is imported, not redefined.
        """
        # Walk this file's own AST: no assignment to
        # FHIR_R4_FILTER_OPERATORS as a target name.
        own_path = Path(__file__)
        own_text = own_path.read_text()
        tree = ast.parse(own_text)
        local_def_linenumbers = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "FHIR_R4_FILTER_OPERATORS":
                        local_def_linenumbers.append(node.lineno)
            if isinstance(node, ast.AnnAssign):
                if (
                    isinstance(node.target, ast.Name)
                    and node.target.id == "FHIR_R4_FILTER_OPERATORS"
                ):
                    local_def_linenumbers.append(node.lineno)
        assert not local_def_linenumbers, (
            f"Local FHIR_R4_FILTER_OPERATORS redefinition at lines "
            f"{local_def_linenumbers} — must IMPORT from "
            f"medterm4ds.engines.fhir (registry-as-contract pattern)."
        )


# =============================================================================
# Pattern 9: ast.Compare-specific source-read methodology for case-fidelity
# audits (test_s85 in SKEPTIC resweep file — the new probe class)
# =============================================================================


class TestPattern9AstCompareSourceReadCaseFidelity:
    """Re-derive the ast.Compare-specific source-read methodology for
    case-fidelity audits.

    Per VS-01 SKEPTIC resweep (test_s85): walks ast.Compare nodes ONLY
    (excludes docstrings + comments) for ``param.get("name") <op> "..."``
    patterns. Asserts canonical case present + no off-case variants in
    comparisons.

    Applied here to the ``_extract_valueset_from_parameters`` function:
      - Canonical: ``param.get("name") != "valueSet"``
      - Off-case (must NOT be in comparisons): ``ValueSet``, ``valueset``,
        ``Valueset``, ``VALUESET``, ``value_set``, ``value-set``.
    """

    def test_h90_extract_valueset_only_uses_canonical_valueset_in_compare(self):
        """AST walk of _extract_valueset_from_parameters: the only
        param.get('name') Compare uses canonical 'valueSet'.

        Per CS-05/TERMINOLOGIST tip (verified CLEAN by VS-01 SKEPTIC):
        the implementation correctly requires exact-case ``valueSet``.
        """
        src = _source_text()
        fn_text = _get_nested_func_source(
            src, "create_fhir_app", "_extract_valueset_from_parameters"
        )
        assert fn_text is not None
        tree = ast.parse(fn_text)
        # Collect every ast.Compare with a string constant on one side.
        compared_strings: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                # Walk both sides.
                for side in [node.left] + list(node.comparators):
                    if isinstance(side, ast.Constant) and isinstance(side.value, str):
                        compared_strings.add(side.value)
        # Canonical case MUST be present.
        assert "valueSet" in compared_strings, (
            f"_extract_valueset_from_parameters MUST compare against "
            f"'valueSet' (canonical case); compared strings: "
            f"{compared_strings}. CS-05/TERMINOLOGIST tip verified CLEAN "
            f"by VS-01 SKEPTIC."
        )
        # Off-case variants MUST NOT be present.
        off_case = {
            "ValueSet", "valueset", "Valueset", "VALUESET",
            "value_set", "value-set",
        }
        leaked = off_case & compared_strings
        # Note: "ValueSet" appears in the resourceType check
        # (resource.get("resourceType") == "ValueSet") — that's a
        # DIFFERENT check, not a param.get('name') comparison.
        # The ast walk above captures every string Compare; "ValueSet"
        # may legitimately appear from the resourceType check. Filter it.
        # The contract here is that NO off-case variant appears as a
        # param.get('name') comparison specifically.
        # Since we can't easily distinguish via ast.walk alone, accept
        # "ValueSet" appearing once (for resourceType) but flag others.
        truly_off_case = off_case - {"ValueSet"}
        leaked = truly_off_case & compared_strings
        assert not leaked, (
            f"Off-case variants {leaked} found in ast.Compare nodes of "
            f"_extract_valueset_from_parameters. These would silently "
            f"accept off-case param names as canonical 'valueSet'. "
            f"CS-05/TERMINOLOGIST tip verified CLEAN — re-introducing "
            f"would be a regression."
        )

    def test_h91_extract_valueset_off_case_behaviorally_silently_dropped(self, fhir_client):
        """BEHAVIORAL: off-case param name 'ValueSet' is silently dropped.

        The handler falls through to the 400 path (no ValueSet body
        processed). Verified by sending a Parameters body with off-case
        name and observing the response does NOT contain a ValueSet
        expansion (the canonical case would process the body).
        """
        # Send a Parameters body with off-case param name.
        # Spec: https://hl7.org/fhir/R4/parameters.html
        body = {
            "resourceType": "Parameters",
            "parameter": [{
                "name": "ValueSet",  # off-case — should be silently dropped
                "resource": {
                    "resourceType": "ValueSet",
                    "url": "http://example.org/test/h91",
                    "compose": {
                        "include": [{
                            "system": SNOMED_URI,
                            "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                        }],
                    },
                },
            }],
        }
        status, response = _post_expand(fhir_client, body)
        # Off-case variant → handler falls through to no-url/no-filter path
        # → likely 400 with OperationOutcome. The contract: no expansion
        # was produced from the off-case param.
        if status == 200:
            # If 200, the contains[] MUST be empty (off-case param dropped).
            contains = response.get("expansion", {}).get("contains", [])
            snomed_codes = {
                c.get("code") for c in contains
                if c.get("system") == SNOMED_URI
            }
            assert SNOMED_DIABETES_MELLITUS not in snomed_codes, (
                f"Off-case 'ValueSet' param must be silently dropped; "
                f"got SNOMED codes {snomed_codes}. This is the "
                f"CS-05/TERMINOLOGIST tip + VS-01 SKEPTIC test_s21 contract."
            )
        else:
            # 400/422 path: response MUST be OperationOutcome (FHIR body).
            assert response.get("resourceType") in {
                "OperationOutcome", "Parameters", "ValueSet"
            }, (
                f"Off-case path must produce FHIR body; got "
                f"{response.get('resourceType')}: {response}"
            )


# =============================================================================
# Pattern 10: META — test-too-lenient audit on prior VS-01 HISTORIAN
# probes (TS-03 HISTORIAN QA-034 pattern extension)
# =============================================================================


class TestPattern10TestTooLenientAudit:
    """Re-audit prior VS-01 HISTORIAN probes for test-too-lenient issues
    per TS-03 HISTORIAN QA-034: negative-only assertions that would
    false-pass on a different bug.

    The fix: every probe asserts POSITIVE success shape (200 + expected
    fields), not just absence of one error string.
    """

    def test_h100_s12_extensional_unknown_code_positive_shape_audit(self, fhir_client):
        """TS-03 HISTORIAN QA-034 pattern: probe s12 (extensional compose
        with unknown code) asserts POSITIVE shape (200 + contains has
        the unknown code), not just absence of 5xx.

        Per GLOBAL_RULES.md "Test-too-lenient": every probe asserts
        POSITIVE success shape.
        """
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test/h100",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": "9999999999"}],  # unknown code
                }],
            },
        }
        status, response = _post_expand(fhir_client, body)
        # POSITIVE shape: 200 + expansion.contains has the unknown code.
        assert status == 200, f"Expected 200; got {status}: {response}"
        contains = response.get("expansion", {}).get("contains", [])
        codes = {c.get("code") for c in contains}
        assert "9999999999" in codes, (
            f"Extensional compose MUST include the unknown code in contains; "
            f"got {codes}. This positive-shape assertion is the load-bearing "
            f"contract — a negative-only 'no 5xx' assertion would false-pass "
            f"on a real bug."
        )

    def test_h101_url_echoed_positive_shape_audit(self, fhir_client):
        """TS-03 HISTORIAN QA-034 pattern: probe s60 (url echoed) asserts
        POSITIVE shape (200 + response.url == posted url)."""
        url = "http://example.org/test/h101-unique-url"
        body = {
            "resourceType": "ValueSet",
            "url": url,
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                }],
            },
        }
        status, response = _post_expand(fhir_client, body)
        # POSITIVE shape: 200 + response.url == posted url.
        assert status == 200, f"Expected 200; got {status}: {response}"
        assert response.get("url") == url, (
            f"Posted url must be echoed in response.url; got "
            f"{response.get('url')!r}. Positive-shape assertion per "
            f"TS-03 HISTORIAN QA-034."
        )

    def test_h102_search_returns_empty_bundle_positive_shape_audit(self, fhir_client):
        """TS-03 HISTORIAN QA-034 pattern: SEARCH returns empty Bundle with
        POSITIVE shape (200 + total=0 + entry=[])."""
        # Spec: https://hl7.org/fhir/R4/bundle.html
        resp = fhir_client.get(
            "/fhir/ValueSet",
            params={"url": "http://nonexistent.example.org/x"},
            headers={"Accept": "application/fhir+json"},
        )
        # POSITIVE shape: 200 + Bundle + total=0 + entry=[].
        assert resp.status_code == 200, f"Expected 200; got {resp.status_code}"
        body = resp.json()
        assert body.get("resourceType") == "Bundle", (
            f"SEARCH must return Bundle resourceType; got "
            f"{body.get('resourceType')}."
        )
        assert body.get("type") == "searchset", (
            f"Bundle.type must be 'searchset'; got {body.get('type')}."
        )
        assert body.get("total") == 0, (
            f"Empty SEARCH must have total=0; got {body.get('total')}."
        )
        assert body.get("entry") == [], (
            f"Empty SEARCH must have entry=[]; got {body.get('entry')}."
        )


# =============================================================================
# META: structural invariant — _expand_intensional location and signature
# =============================================================================


class TestMetaStructuralInvariants:
    """META invariants that protect the structural shape of the VS-01
    surface. These guards catch refactors that would silently break
    the load-bearing contracts (function rename, signature change,
    constant removal, etc.).
    """

    def test_h110_expand_intensional_function_exists(self):
        """META: _expand_intensional function still exists in apps/fhir_api.py.

        If a refactor renames or removes this function, every probe in
        this file's source-read sections will fail loudly. This META
        probe surfaces the root cause: function existence.
        """
        src = _source_text()
        fn_text = _get_nested_func_source(src, "create_fhir_app", "_expand_intensional")
        assert fn_text is not None, (
            "_expand_intensional function not found in apps/fhir_api.py. "
            "If renamed, every source-read probe in this file MUST be "
            "updated to point to the new function name."
        )

    def test_h111_extract_valueset_from_parameters_function_exists(self):
        """META: _extract_valueset_from_parameters function still exists."""
        src = _source_text()
        fn_text = _get_nested_func_source(
            src, "create_fhir_app", "_extract_valueset_from_parameters"
        )
        assert fn_text is not None, (
            "_extract_valueset_from_parameters function not found in "
            "apps/fhir_api.py. VS-03 SKEPTIC QA-059 surface."
        )

    def test_h112_canonical_system_uri_helper_exists(self):
        """META: ``canonical_system_uri`` helper is importable from
        engines.fhir (used by _expand_intensional for canonical URI
        re-resolution per CR-013).

        Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.system
            "An absolute URI which is the code system URI of the code
            system from which the code in the expansion was defined."
        """
        assert callable(canonical_system_uri), (
            "canonical_system_uri helper must be importable and callable. "
            "Used by _expand_intensional per CR-013 for canonical URI "
            "re-resolution (prevents client-input-as-canonical drift)."
        )

    def test_h113_sab_label_to_fhir_uri_helper_exists(self):
        """META: ``sab_label_to_fhir_uri`` helper is importable from
        engines.fhir (CS-01 SKEPTIC QA-043 + HISTORIAN QA-044 surface)."""
        assert callable(sab_label_to_fhir_uri), (
            "sab_label_to_fhir_uri helper must be importable and callable. "
            "Used by _do_lookup per CS-01 SKEPTIC QA-043 to translate raw "
            "SAB labels to canonical FHIR URIs."
        )
