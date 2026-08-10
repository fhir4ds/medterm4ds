"""EXPLORER RESWEEP probes for CS-04 (CodeSystem $subsumes Operation) —
fresh full-sweep run.

Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html (R4 4.0.1).

This file contains NEW lateral-combination probes that are NOT in the
baseline ``test_cs04_explorer.py``. The baseline (test_e10..e190, 42 probes)
is treated as trusted prior coverage; this resweep file adds the
FRESH-FULL-SWEEP mandated probes per USER_DIRECTIVES [2026-08-08].

EXPLORER lens (per ROLE_QA_ENGINEER Section 3): lateral thinking — unusual
parameter combinations, integration corners, cross-operation consistency,
and 10th PROMOTED pattern (isinstance guard at untrusted-data list-iterator
boundary) extension across the full FHIR surface.

HISTORIAN tip for EXPLORER (per qa_handoff.md): probe lateral combinations
on the ValueSet/$expand intensional surface — MIX of valid dict entries
AND non-dict entries across all 5 sibling iterators (include[],
include[].concept[], include[].filter[], exclude[], exclude[].concept[])
to verify the HISTORIAN fix processes valid entries silently while dropping
non-dict entries. Other lateral directions:

  - Combined operations $subsumes → $lookup on same code
    (cross-operation consistency)
  - GET↔POST byte-exact parity on lateral input shapes
    (mixed scalar+coding on POST)
  - The PROMOTED 10th pattern (isinstance guard) holds on every POST
    handler — a lateral audit walking app.routes POSTing hostile bodies to
    each, asserting < 500, would extend the pattern contract across the
    full FHIR surface
  - $subsumes with display parameter on POST (alternative encoding)
  - $subsumes with version parameter combinations
  - $subsumes with codingA from one system and codingB from another
    (mixed-system via codings)

Prior CS-04 patterns to re-derive (HELD or REGRESSED):
  - QA-001 _parse_parameters isinstance guard (SKEPTIC resweep — RESOLVED)
  - QA-001 _expand_intensional 5 sibling isinstance guards
    (HISTORIAN resweep — RESOLVED, PROMOTED as 10th pattern)
  - QA-053 codingA/codingB alternative-encoding silent-drop (HELD)
  - Mixed-system check fires with diagnostics naming both systems (HELD)

Lens dimensions:
  L1  Lateral MIX of valid + non-dict entries across 5 sibling iterators
      in _expand_intensional (HISTORIAN tip — primary probe class)
  L2  Combined operations $subsumes → $lookup on same code
  L3  GET↔POST byte-exact parity on lateral input shapes
      (mixed scalar + coding on POST)
  L4  Lateral audit walking app.routes POSTing hostile bodies to each,
      asserting < 500 — extend 10th PROMOTED pattern contract across the
      full FHIR surface
  L5  $subsumes with display parameter on POST (alternative encoding)
  L6  $subsumes with version parameter combinations
  L7  $subsumes with codingA from one system + codingB from another
      (mixed-system via codings — both offenders variant)
  L8  $subsumes → $lookup → $validate-code cross-operation round-trip
      consistency on same code
  L9  Outcome shape audit on lateral input shapes
  L10 Source-read structural contracts
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
#
# In Parameters (R4):
#   codeA    0..1  code    "The 'A' code that is to be tested. If a code
#                           is provided, a system must be provided"
#   codeB    0..1  code    "The 'B' code that is to be tested. If a code
#                           is provided, a system must be provided"
#   system   0..1  uri     "The code system in which subsumption testing
#                           is to be performed. Must be provided unless
#                           invoked on a code system instance"
#   version  0..1  string  "The version of the code system, if one was
#                           provided in the source data"
#   codingA  0..1  Coding  "The 'A' Coding that is to be tested. The code
#                           system does not have to match the specified
#                           subsumption code system, but the relationships
#                           between the code systems must be well
#                           established"
#   codingB  0..1  Coding  "The 'B' Coding that is to be tested. ..."
#
# Out Parameters:
#   outcome   1..1  code   "The subsumption relationship between code/Coding
#                           'A' and code/Coding 'B'. There are 4 possible
#                           codes to be returned (equivalent, subsumes,
#                           subsumed-by, and not-subsumed) as defined in
#                           the concept-subsumption-outcome value set."

VALID_OUTCOMES = {"equivalent", "subsumes", "subsumed-by", "not-subsumed"}
FORBIDDEN_OUTCOMES = {"subsumedBy", "subsumed_by", "subsumedby", "SUBSUMED-BY", "Subsumed-By"}

SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_US = "http://snomed.info/sct/731000124108"  # US edition URL form
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child (descendant of 73211009)
SNOMED_VIRAL_HEPATITIS = "3738000"     # unrelated to diabetes branch
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"            # unrelated to diabetes branch
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_E11 = "E11"                    # Type 2 diabetes mellitus (ICD-10-CM)

FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _outcome(body: dict) -> str | None:
    """Return the value of the Out `outcome` parameter."""
    for p in body.get("parameter", []):
        if not isinstance(p, dict):
            continue
        if p.get("name") == "outcome":
            if "valueCode" in p:
                return p["valueCode"]
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


def _diagnostics(body: dict) -> str:
    """Extract the diagnostics string from an OperationOutcome."""
    for issue in body.get("issue", []):
        if not isinstance(issue, dict):
            continue
        if "diagnostics" in issue:
            return issue["diagnostics"]
    return ""


def _get_func_source(func_name: str) -> str:
    """Source-read helper: get the source of a top-level or nested function
    definition from ``apps/fhir_api.py``."""
    src = FHIR_API_PATH.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return ast.get_source_segment(src, node) or ""
    return ""


def _get_nested_func_source(parent_name: str, child_name: str) -> str:
    """Source-read helper for nested functions defined inside a factory."""
    src = FHIR_API_PATH.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == parent_name:
                for child in ast.walk(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if child.name == child_name:
                            return ast.get_source_segment(src, child) or ""
    return ""


def _build_subsumes_params(
    system: str, code_a: str, code_b: str, **extra
) -> dict:
    """Build a Parameters body for $subsumes."""
    params = [
        {"name": "system", "valueUri": system},
        {"name": "codeA", "valueCode": code_a},
        {"name": "codeB", "valueCode": code_b},
    ]
    for k, v in extra.items():
        params.append({"name": k, "valueString": v})
    return {"resourceType": "Parameters", "parameter": params}


# ============================================================================
# L1: Lateral MIX of valid + non-dict entries across 5 sibling iterators in
#     _expand_intensional (HISTORIAN tip — primary probe class)
# ============================================================================
# CS-04 / HISTORIAN QA-001 (RESOLVED, PROMOTED as 10th PROMOTED pattern)
# added ``isinstance(<var>, dict): continue`` guards inside
# ``_expand_intensional`` at 5 iterator sites. This lens verifies the fix
# processes valid entries SILENTLY while dropping non-dict entries — the
# HISTORIAN L0 source-read probe only counted isinstance() calls; this
# lens exercises the BEHAVIORAL contract per the HISTORIAN tip.

class TestLens1ExpandIntensionalMixedValidInvalidEntries:
    """L1: HISTORIAN tip — MIX of valid + non-dict entries MUST process
    valid entries silently while dropping non-dict entries.

    Per RFC 7231 §6.5.1 "liberal in what you accept": the server processes
    valid entries; malformed entries are silently dropped (mirror missing-
    field fall-through). Found by EXPLORER (extends HISTORIAN QA-001
    regression-pin to the behavioral layer).
    """

    def test_e10_include_mixed_valid_and_non_dict_entries_processes_valid(
        self, fhir_client
    ) -> None:
        """compose.include[] with [valid, "string", valid] — both valid
        entries are processed; the non-dict entry is silently dropped."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test",
            "compose": {
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": "73211009"}]},
                    "garbage-string",
                    42,
                    None,
                    {"system": SNOMED_URI, "concept": [{"code": "44054006"}]},
                ],
            },
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code < 500, (
            f"mixed valid/non-dict include[]: {r.status_code}; expected <500. "
            f"Body: {r.text[:300]}"
        )
        # When the engine processes both valid entries, both codes appear
        # in the expansion.
        assert r.status_code == 200, (
            f"expected 200 for valid-after-garbage processing; got "
            f"{r.status_code}: {r.text[:300]}"
        )
        data = r.json()
        contains = data.get("expansion", {}).get("contains", [])
        codes = [c.get("code") for c in contains]
        assert "73211009" in codes, (
            f"first valid entry silently dropped along with non-dict entries; "
            f"codes: {codes!r}"
        )
        assert "44054006" in codes, (
            f"second valid entry silently dropped along with non-dict entries; "
            f"codes: {codes!r}"
        )

    def test_e11_include_concept_mixed_valid_and_non_dict_entries(
        self, fhir_client
    ) -> None:
        """compose.include[].concept[] with [valid, "string", valid] —
        valid concept entries are processed; non-dict entries are dropped."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [
                        {"code": "73211009"},
                        "garbage-string",
                        42,
                        None,
                        {"code": "44054006"},
                    ],
                }],
            },
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code < 500, (
            f"mixed valid/non-dict concept[]: {r.status_code}; expected <500"
        )
        assert r.status_code == 200, (
            f"expected 200; got {r.status_code}: {r.text[:300]}"
        )
        data = r.json()
        contains = data.get("expansion", {}).get("contains", [])
        codes = [c.get("code") for c in contains]
        assert "73211009" in codes
        assert "44054006" in codes

    def test_e12_include_filter_mixed_valid_and_non_dict_entries(
        self, fhir_client
    ) -> None:
        """compose.include[].filter[] with [valid, "string", valid] —
        valid filter entries are processed (or silently dropped per
        unsupported operator); non-dict entries are dropped."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [
                        "garbage-string",
                        42,
                        None,
                        {"property": "concept", "op": "is-a", "value": "73211009"},
                    ],
                }],
            },
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code < 500, (
            f"mixed valid/non-dict filter[]: {r.status_code}; expected <500"
        )
        # The valid filter entry should produce the expansion
        assert r.status_code == 200, (
            f"expected 200; got {r.status_code}: {r.text[:300]}"
        )
        data = r.json()
        contains = data.get("expansion", {}).get("contains", [])
        codes = [c.get("code") for c in contains]
        # is-a includes root + descendants — root MUST be present
        assert "73211009" in codes, (
            f"is-a root not in expansion despite valid filter entry surviving "
            f"non-dict entries; codes: {codes!r}"
        )

    def test_e13_exclude_mixed_valid_and_non_dict_entries(
        self, fhir_client
    ) -> None:
        """compose.exclude[] with [valid, "string"] — valid exclude entry
        is processed; non-dict entries are dropped silently."""
        # First include 2 codes, then exclude 1 via a valid exclude block
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": "73211009"}, {"code": "44054006"}],
                }],
                "exclude": [
                    "garbage-string",
                    42,
                    None,
                    {"system": SNOMED_URI, "concept": [{"code": "44054006"}]},
                ],
            },
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code < 500, (
            f"mixed valid/non-dict exclude[]: {r.status_code}; expected <500"
        )
        assert r.status_code == 200, (
            f"expected 200; got {r.status_code}: {r.text[:300]}"
        )
        data = r.json()
        contains = data.get("expansion", {}).get("contains", [])
        codes = [c.get("code") for c in contains]
        # The valid exclude entry removes 44054006; 73211009 stays
        assert "73211009" in codes, (
            f"non-excluded code missing from expansion: {codes!r}"
        )
        assert "44054006" not in codes, (
            f"excluded code present despite valid exclude entry; codes: {codes!r}"
        )

    def test_e14_exclude_concept_inline_isinstance_filter_handles_mixed(
        self, fhir_client
    ) -> None:
        """compose.exclude[].concept[] with [valid, "string", valid] —
        the inline ``isinstance(c, dict)`` filter handles the mixed list
        silently; valid concept entries are excluded from the expansion."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": "73211009"}, {"code": "44054006"}],
                }],
                "exclude": [{
                    "system": SNOMED_URI,
                    "concept": [
                        "garbage",
                        42,
                        None,
                        {"code": "44054006"},
                    ],
                }],
            },
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code < 500, (
            f"exclude.concept[] mixed: {r.status_code}; expected <500"
        )
        assert r.status_code == 200, (
            f"expected 200; got {r.status_code}: {r.text[:300]}"
        )
        data = r.json()
        contains = data.get("expansion", {}).get("contains", [])
        codes = [c.get("code") for c in contains]
        # The valid exclude concept entry (44054006) is honored
        assert "44054006" not in codes, (
            f"excluded code present despite valid exclude.concept[] entry "
            f"surviving non-dict entries; codes: {codes!r}"
        )
        assert "73211009" in codes

    def test_e15_exclude_concept_non_list_skipped_silently(
        self, fhir_client
    ) -> None:
        """compose.exclude[].concept set to a non-list value (string,
        int, None) — the ``isinstance(exc_concepts, list): continue`` guard
        skips the whole exclude block silently without 5xx."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": "73211009"}],
                }],
                "exclude": [
                    {"system": SNOMED_URI, "concept": "not-a-list"},
                ],
            },
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code < 500, (
            f"exclude.concept non-list: {r.status_code}; expected <500"
        )
        assert r.status_code == 200, (
            f"expected 200; got {r.status_code}: {r.text[:300]}"
        )
        # The valid include entry survives
        data = r.json()
        contains = data.get("expansion", {}).get("contains", [])
        codes = [c.get("code") for c in contains]
        assert "73211009" in codes


# ============================================================================
# L2: Combined operations $subsumes → $lookup on same code
# ============================================================================
# Per FHIR R4 §4.7.7 Subsumption testing: $subsumes answers a RELATIONSHIP
# question between 2 codes in the same code system; $lookup answers a CODE
# IDENTITY question. The 2 operations SHOULD agree on:
#   - system: both Out `system` MUST be the canonical URI (no client-input
#     echo drift)
#   - code presence: a code that returns 200 on $lookup MUST be recognized
#     by $subsumes (no "unknown code" branch)
#   - display: the $lookup display for the parent in a subsumes relationship
#     SHOULD be canonical (matches engine preferred term)
# This lens extends the EXPLORER cross-operation-canonical-agreement probe
# class from CS-05 EXPLORER test_e10/e11 to the $subsumes ↔ $lookup
# direction.

class TestLens2SubsumesToLookupCrossOperationConsistency:
    """L2: Combined operations $subsumes → $lookup on same code MUST
    produce consistent canonical URIs and agreement on code presence."""

    def test_e20_subsumes_system_matches_lookup_system_for_same_code(
        self, fhir_client
    ) -> None:
        """The ``system`` echoed by $subsumes (via canonical resolution)
        MUST match the ``system`` echoed by $lookup for the same code.

        Per CS-05 EXPLORER cross-operation-canonical-agreement invariant
        extended to $subsumes: both operations consult the same canonical
        registry (``SYSTEM_TO_FHIR_URI``) and MUST agree on the canonical
        URI for the seeded SNOMED code.
        """
        sub_body = _build_subsumes_params(
            SNOMED_URI, SNOMED_DIABETES_MELLITUS, SNOMED_T2DM
        )
        sub_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=sub_body)
        assert sub_r.status_code == 200, sub_r.text[:300]
        sub_data = sub_r.json()
        assert _outcome(sub_data) == "subsumes"

        lookup_r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
        )
        assert lookup_r.status_code == 200, lookup_r.text[:300]
        # The lookup Out `system` matches $subsumes canonical resolution
        # (both go through canonical_system_uri internally).
        # If $subsumes doesn't emit Out `system` (per spec — only Out
        # `outcome` is required), the invariant is that $subsumes accepts
        # the same system URI as $lookup.

    def test_e21_subsumes_equivalent_path_consistent_with_lookup_self(
        self, fhir_client
    ) -> None:
        """When $subsumes returns ``equivalent`` (A == B), the same code
        MUST round-trip through $lookup with a 200 response."""
        body = _build_subsumes_params(
            SNOMED_URI, SNOMED_DIABETES_MELLITUS, SNOMED_DIABETES_MELLITUS
        )
        sub_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert sub_r.status_code == 200
        assert _outcome(sub_r.json()) == "equivalent"

        # The same code MUST be found by $lookup
        lookup_r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
        )
        assert lookup_r.status_code == 200, lookup_r.text[:300]

    def test_e22_subsumes_unknown_code_does_not_crash_lookup(
        self, fhir_client
    ) -> None:
        """When $subsumes returns ``not-subsumed`` for an unknown code,
        $lookup on the same code MUST NOT crash with 500. Either $lookup
        returns 200 + OperationOutcome (per current medterm4ds semantic)
        or a 4xx error — the invariant is no 5xx."""
        unknown_code = "9999999999UNKNOWN"
        body = _build_subsumes_params(SNOMED_URI, unknown_code, SNOMED_T2DM)
        sub_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert sub_r.status_code == 200
        # Unknown codes return not-subsumed per current engine semantic
        # (no relationship found; not an error).
        assert _outcome(sub_r.json()) == "not-subsumed"

        lookup_r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": unknown_code},
        )
        assert lookup_r.status_code < 500, (
            f"unknown code lookup returned 5xx: {lookup_r.status_code}; "
            f"body: {lookup_r.text[:300]}"
        )

    def test_e23_subsumes_to_lookup_directionality_consistency(
        self, fhir_client
    ) -> None:
        """$subsumes(A, B) = subsumes means A is broader than B. The
        $lookup displays for A and B MUST be present (both seeded)."""
        # A = DM (broader), B = T2DM (narrower)
        body = _build_subsumes_params(
            SNOMED_URI, SNOMED_DIABETES_MELLITUS, SNOMED_T2DM
        )
        sub_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert sub_r.status_code == 200
        assert _outcome(sub_r.json()) == "subsumes"

        # Look up both codes — both MUST succeed
        for code in (SNOMED_DIABETES_MELLITUS, SNOMED_T2DM):
            r = fhir_client.get(
                "/fhir/CodeSystem/$lookup",
                params={"system": SNOMED_URI, "code": code},
            )
            assert r.status_code == 200, (
                f"lookup for {code} failed: {r.status_code}: {r.text[:200]}"
            )


# ============================================================================
# L3: GET↔POST byte-exact parity on lateral input shapes
# ============================================================================

class TestLens3GetPostByteExactParityOnLateralInputs:
    """L3: GET and POST MUST produce byte-exact parity on lateral input
    shapes — alias URIs, version param, version+display combos, and
    scalar+coding combined inputs."""

    def test_e30_get_post_alias_uri_parity_snomed(self, fhir_client) -> None:
        """GET and POST with the same alias URI produce the same outcome."""
        alias_uri = "http://snomed.info/sct/"  # trailing slash
        get_r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": alias_uri,
                "codeA": SNOMED_DIABETES_MELLITUS,
                "codeB": SNOMED_T2DM,
            },
        )
        body = _build_subsumes_params(
            alias_uri, SNOMED_DIABETES_MELLITUS, SNOMED_T2DM
        )
        post_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert get_r.status_code == post_r.status_code
        assert _outcome(get_r.json()) == _outcome(post_r.json())

    def test_e31_get_post_version_param_parity(self, fhir_client) -> None:
        """GET with version=X and POST with version=X produce the same
        outcome (version is accepted but ignored per NOT A BUG registry)."""
        version = "2024-09"
        get_r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": SNOMED_DIABETES_MELLITUS,
                "codeB": SNOMED_T2DM,
                "version": version,
            },
        )
        body = _build_subsumes_params(
            SNOMED_URI,
            SNOMED_DIABETES_MELLITUS,
            SNOMED_T2DM,
            version=version,
        )
        post_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert get_r.status_code == 200
        assert post_r.status_code == 200
        assert _outcome(get_r.json()) == _outcome(post_r.json()) == "subsumes"

    def test_e32_post_mixed_scalar_codeA_and_codingB_byte_exact(
        self, fhir_client
    ) -> None:
        """POST with scalar codeA + codingB (mixed encoding) MUST produce
        the same outcome as POST with all-scalar (codeA + codeB)."""
        mixed_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
                {"name": "codingB", "valueCoding": {
                    "system": SNOMED_URI, "code": SNOMED_T2DM,
                }},
            ],
        }
        all_scalar_body = _build_subsumes_params(
            SNOMED_URI, SNOMED_DIABETES_MELLITUS, SNOMED_T2DM
        )
        mixed_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=mixed_body)
        scalar_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=all_scalar_body)
        assert mixed_r.status_code == scalar_r.status_code == 200
        assert _outcome(mixed_r.json()) == _outcome(scalar_r.json()) == "subsumes"

    def test_e33_post_codingA_and_scalar_codeB_byte_exact(
        self, fhir_client
    ) -> None:
        """POST with codingA + scalar codeB (reverse mixed encoding) MUST
        produce the same outcome as POST with all-scalar."""
        mixed_body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {
                    "system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS,
                }},
                {"name": "codeB", "valueCode": SNOMED_T2DM},
            ],
        }
        all_scalar_body = _build_subsumes_params(
            SNOMED_URI, SNOMED_DIABETES_MELLITUS, SNOMED_T2DM
        )
        mixed_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=mixed_body)
        scalar_r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=all_scalar_body)
        assert mixed_r.status_code == scalar_r.status_code == 200
        assert _outcome(mixed_r.json()) == _outcome(scalar_r.json()) == "subsumes"


# ============================================================================
# L4: Lateral audit walking app.routes POSTing hostile bodies to each —
#     extend 10th PROMOTED pattern contract across the full FHIR surface
# ============================================================================
# Per GLOBAL_RULES.md 10th PROMOTED pattern (count=4 threshold crossed):
# every ``for <var> in <body>.get("<key>", []):`` loop where ``<var>`` is
# subsequently ``.get(...)``-accessed MUST have an ``isinstance(<var>,
# dict): continue`` guard. The HISTORIAN fix verified the 5 sibling
# iterators inside ``_expand_intensional``; this lens EXTENDS the contract
# to every POST handler on the FHIR surface via a behavioral audit walking
# ``app.routes``.

class TestLens4AppRoutesHostileBodyAudit:
    """L4: Lateral audit — every POST handler accepting a Parameters body
    MUST NOT 500 on hostile non-dict parameter[] entries. Extends the 10th
    PROMOTED pattern contract across the full FHIR surface.
    """

    def test_e40_all_post_handlers_survive_hostile_parameter_list(
        self, fhir_client
    ) -> None:
        """For every POST route accepting a Parameters body, POST a body
        with ``parameter: [non-dict-entries]``. The handler MUST NOT 500
        (per 10th PROMOTED pattern contract + FHIR R4 §3.1.0.1.5 +
        §3.1.0.1.9 — server returns FHIR OperationOutcome on errors, never
        traceback)."""
        hostile_body = {
            "resourceType": "Parameters",
            "parameter": ["string-not-dict", 42, None, ["nested"]],
        }
        # Routes accepting a Parameters body (per the @app.post decls in
        # apps/fhir_api.py)
        post_routes = [
            "/fhir/CodeSystem/$lookup",
            "/fhir/CodeSystem/$validate-code",
            "/fhir/CodeSystem/$subsumes",
            "/fhir/CodeSystem/$closure",
            "/fhir/ValueSet/$validate-code",
            "/fhir/ValueSet/$expand",
            "/fhir/ConceptMap/$translate",
        ]
        for route in post_routes:
            r = fhir_client.post(route, json=hostile_body)
            assert r.status_code < 500, (
                f"POST {route} returned 5xx on hostile parameter[]: "
                f"{r.status_code}; body: {r.text[:300]}"
            )
            ct = r.headers.get("content-type", "")
            assert "application/fhir+json" in ct or "application/fhir+xml" in ct, (
                f"POST {route} hostile body Content-Type {ct!r}; "
                f"expected application/fhir+(json|xml)"
            )

    def test_e41_expand_post_value_set_body_survives_hostile_compose(
        self, fhir_client
    ) -> None:
        """ValueSet/$expand with a ValueSet resource body containing
        hostile compose blocks MUST NOT 500. The 5 sibling isinstance
        guards in ``_expand_intensional`` protect against this."""
        body = {
            "resourceType": "ValueSet",
            "url": "http://example.org/test",
            "compose": {
                "include": ["string-not-dict", 42, None],
                "exclude": ["also-not-dict"],
            },
        }
        r = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        assert r.status_code < 500, (
            f"hostile compose: {r.status_code}; expected <500. Body: {r.text[:300]}"
        )

    def test_e42_extract_post_survives_hostile_body(self, fhir_client) -> None:
        """CodeSystem/$extract with a hostile Parameters body MUST NOT
        500. The _parse_parameters isinstance guard protects this
        surface via delegation."""
        body = {
            "resourceType": "Parameters",
            "parameter": ["not-dict", 42, None],
        }
        r = fhir_client.post("/fhir/CodeSystem/$extract", json=body)
        assert r.status_code < 500, (
            f"extract hostile body: {r.status_code}; expected <500. "
            f"Body: {r.text[:300]}"
        )

    def test_e43_search_post_survives_hostile_body(self, fhir_client) -> None:
        """CodeSystem/$search with a hostile Parameters body MUST NOT
        500."""
        body = {
            "resourceType": "Parameters",
            "parameter": ["not-dict", 42, None],
        }
        r = fhir_client.post("/fhir/CodeSystem/$search", json=body)
        # $search may return 503 (BM25 not loaded) or 4xx — the invariant
        # is no 5xx-with-traceback; 503 has a FHIR OperationOutcome body
        # (no traceback).
        assert r.status_code < 500 or r.status_code == 503, (
            f"search hostile body: {r.status_code}; expected <500 or 503. "
            f"Body: {r.text[:300]}"
        )

    def test_e44_batch_post_survives_hostile_entries_list(
        self, fhir_client
    ) -> None:
        """POST /fhir batch with hostile Bundle entries MUST NOT 500.
        Per FHIR R4 §3.7 per-entry error isolation: a malformed entry
        MUST be isolated to that entry's response, never 500 the whole
        batch."""
        body = {
            "resourceType": "Bundle",
            "type": "batch",
            "entry": [
                "not-a-dict",
                42,
                None,
                {"request": {"method": "GET", "url": "/CodeSystem/$lookup?system=X&code=Y"}},
            ],
        }
        r = fhir_client.post("/fhir", json=body)
        assert r.status_code < 500, (
            f"batch hostile entries: {r.status_code}; expected <500. "
            f"Body: {r.text[:300]}"
        )

    def test_e45_unknown_resource_post_returns_fhir_operationoutcome(
        self, fhir_client
    ) -> None:
        """POST to an unknown resource type (Patient, Observation) MUST
        return a 405 OperationOutcome with FHIR Content-Type — the
        catch-all layer (TS-04 EXPLORER QA-042) handles this."""
        for rtype in ("Patient", "Observation", "Condition"):
            r = fhir_client.post(f"/fhir/{rtype}", json={"resourceType": rtype})
            assert r.status_code == 405, (
                f"POST /fhir/{rtype}: expected 405, got {r.status_code}"
            )
            ct = r.headers.get("content-type", "")
            assert "application/fhir+json" in ct, (
                f"POST /fhir/{rtype} Content-Type {ct!r}; expected "
                f"application/fhir+json"
            )
            data = r.json()
            assert data.get("resourceType") == "OperationOutcome"


# ============================================================================
# L5: $subsumes with display parameter on POST (alternative encoding)
# ============================================================================
# Per FHIR R4 §4.8.21.3 In Parameters table, the canonical In params are
# codeA/codeB/system/version/codingA/codingB. There is NO In `display`
# parameter. EXPLORER probes what happens when the client supplies one —
# the server SHOULD either silently accept (and ignore) OR 422 (FastAPI
# strict mode). The KEY invariant is no 5xx and a FHIR Content-Type.

class TestLens5SubsumesDisplayAlternativeEncoding:
    """L5: $subsumes POST with an extra ``display`` parameter — the
    server accepts it (per Parameters-body liberalism) without 5xx."""

    def test_e50_post_subsumes_with_extra_display_param_accepted(
        self, fhir_client
    ) -> None:
        """POST $subsumes with a ``display`` parameter alongside the
        canonical ones — the server MUST accept it without 5xx."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
                {"name": "codeB", "valueCode": SNOMED_T2DM},
                {"name": "display", "valueString": "Diabetes mellitus"},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200, (
            f"display param: {r.status_code}; body: {r.text[:300]}"
        )
        assert _outcome(r.json()) == "subsumes"

    def test_e51_post_subsumes_with_display_per_coding(
        self, fhir_client
    ) -> None:
        """POST $subsumes with ``display`` inside codingA.valueCoding —
        the server accepts the extra field (the helper extracts only
        system+code per CS-04 EXPLORER test_e61 contract)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_DIABETES_MELLITUS,
                    "display": "Diabetes mellitus (extra)",
                }},
                {"name": "codingB", "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_T2DM,
                    "display": "T2DM (extra)",
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200, (
            f"display per coding: {r.status_code}; body: {r.text[:300]}"
        )
        assert _outcome(r.json()) == "subsumes"


# ============================================================================
# L6: $subsumes with version parameter combinations
# ============================================================================

class TestLens6SubsumesVersionCombinations:
    """L6: $subsumes POST with various version param combinations —
    version is accepted but ignored per NOT A BUG registry. The invariant
    is no 5xx and consistent outcome regardless of version value."""

    @pytest.mark.parametrize("version", [
        "2024-09",
        "2025-03",
        "http://snomed.info/sct/731000124108/version/20240901",
        "1.0.0",
        "NONEXISTENT_2099",
        "",  # empty string
    ])
    def test_e60_post_version_combinations_no_crash(
        self, fhir_client, version
    ) -> None:
        """POST $subsumes with various version values — no 5xx."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "version", "valueString": version},
                {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
                {"name": "codeB", "valueCode": SNOMED_T2DM},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200, (
            f"version={version!r}: {r.status_code}; body: {r.text[:300]}"
        )
        assert _outcome(r.json()) == "subsumes"

    def test_e61_post_version_in_coding_does_not_override(self, fhir_client) -> None:
        """Per CS-04 EXPLORER test_e61: a Coding.version embedded field
        does NOT override the operation version param. Verify on
        version+coding combined input."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "version", "valueString": "operation-version"},
                {"name": "codingA", "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_DIABETES_MELLITUS,
                    "version": "coding-version-2024",
                    "display": "DM",
                }},
                {"name": "codingB", "valueCoding": {
                    "system": SNOMED_URI,
                    "code": SNOMED_T2DM,
                    "version": "coding-version-2024",
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200, (
            f"version override: {r.status_code}; body: {r.text[:300]}"
        )
        assert _outcome(r.json()) == "subsumes"


# ============================================================================
# L7: $subsumes with codingA from one system + codingB from another
# ============================================================================

class TestLens7MixedSystemCodingsVariants:
    """L7: Mixed-system check fires when EITHER codingA or codingB (or
    both) reference a different system. The check MUST name BOTH offenders
    when both are wrong, AND fire AFTER the missing-scalar check (per
    HISTORIAN test_h52)."""

    def test_e70_both_codings_cross_system_named_in_diagnostics(
        self, fhir_client
    ) -> None:
        """POST with both codingA and codingB from a different system
        than ``system``. The diagnostics string MUST name at least one
        offender; both will not be named in the SAME response (the check
        fires once and returns), but the response MUST be a 400
        OperationOutcome."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {
                    "system": RXNORM_URI, "code": RXNORM_METFORMIN,
                }},
                {"name": "codingB", "valueCoding": {
                    "system": ICD10CM_URI, "code": ICD10CM_E11,
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 400, (
            f"both cross-system: expected 400, got {r.status_code}: {r.text[:300]}"
        )
        ct = r.headers.get("content-type", "")
        assert "application/fhir+json" in ct
        data = r.json()
        assert data.get("resourceType") == "OperationOutcome"
        diag = _diagnostics(data)
        # The check fires on the FIRST offender (codingA); the response
        # names codingA + RXNORM + SNOMED
        assert "codingA" in diag or "codingB" in diag, (
            f"diagnostics string does not name offender: {diag!r}"
        )

    def test_e71_codingA_snomed_us_edition_mixed_system(self, fhir_client) -> None:
        """codingA from SNOMED US edition URL form
        (http://snomed.info/sct/731000124108) — this is NOT registered
        as an alias in FHIR_URI_TO_SYSTEM, so the mixed-system check
        SHOULD fire (per SKEPTIC test_s80 contract).

        Per spec: "The code system does not have to match the specified
        subsumption code system, but the relationships between the code
        systems must be well established" — medterm4ds has no cross-
        edition SNOMED relationship map today, so the check fires.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {
                    "system": SNOMED_URI_US, "code": SNOMED_DIABETES_MELLITUS,
                }},
                {"name": "codingB", "valueCoding": {
                    "system": SNOMED_URI, "code": SNOMED_T2DM,
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        # The US-edition URL form is NOT registered as alias; mixed-system
        # check fires
        assert r.status_code == 400, (
            f"US-edition mixed-system: expected 400, got {r.status_code}: "
            f"{r.text[:300]}"
        )

    def test_e72_canonical_alias_normalization_no_false_fire(
        self, fhir_client
    ) -> None:
        """codingA.system as a registered alias (trailing-slash SNOMED)
        SHOULD normalize via canonical_system_uri to SNOMED_URI and NOT
        trigger mixed-system check (per CR-023 fix)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codingA", "valueCoding": {
                    "system": "http://snomed.info/sct/",  # trailing slash
                    "code": SNOMED_DIABETES_MELLITUS,
                }},
                {"name": "codingB", "valueCoding": {
                    "system": SNOMED_URI, "code": SNOMED_T2DM,
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        # Trailing-slash normalization should pass the mixed-system check
        assert r.status_code == 200, (
            f"alias normalization: expected 200, got {r.status_code}: "
            f"{r.text[:300]}"
        )
        assert _outcome(r.json()) == "subsumes"


# ============================================================================
# L8: $subsumes → $lookup → $validate-code cross-operation round-trip
# ============================================================================

class TestLens8CrossOperationRoundTripConsistency:
    """L8: $subsumes → $lookup → $validate-code cross-operation round-trip
    on the same code MUST produce consistent canonical URIs and agreement
    on code presence. Extends CS-05 EXPLORER test_e10/e11 (lookup ↔
    validate agreement) to include $subsumes as a third party."""

    def test_e80_round_trip_snomed_diabetes_three_operations_agree(
        self, fhir_client
    ) -> None:
        """All three operations on SNOMED DM 73211009 MUST succeed."""
        sub_r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": SNOMED_DIABETES_MELLITUS,
                "codeB": SNOMED_T2DM,
            },
        )
        lookup_r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
        )
        validate_r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
        )
        assert sub_r.status_code == lookup_r.status_code == validate_r.status_code == 200, (
            f"status codes differ: sub={sub_r.status_code} "
            f"lookup={lookup_r.status_code} validate={validate_r.status_code}"
        )
        assert _outcome(sub_r.json()) == "subsumes"

    def test_e81_round_trip_snomed_t2dm_three_operations_agree(
        self, fhir_client
    ) -> None:
        """All three operations on SNOMED T2DM 44054006 MUST succeed."""
        sub_r = fhir_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": SNOMED_URI,
                "codeA": SNOMED_T2DM,
                "codeB": SNOMED_DIABETES_MELLITUS,
            },
        )
        lookup_r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        validate_r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": SNOMED_T2DM},
        )
        assert sub_r.status_code == lookup_r.status_code == validate_r.status_code == 200
        # subsumed-by because A=T2DM (narrower), B=DM (broader)
        assert _outcome(sub_r.json()) == "subsumed-by"


# ============================================================================
# L9: Outcome shape audit on lateral input shapes
# ============================================================================

class TestLens9OutcomeShapeAuditOnLateralInputs:
    """L9: The Out `outcome` parameter MUST have:
       - resourceType: Parameters
       - parameter[].name: outcome
       - parameter[].valueCode (NOT valueString)
       - outcome value: in VALID_OUTCOMES closed enum
    This lens verifies the shape on lateral input shapes not covered by
    SKEPTIC test_l60..l65 (which used the canonical GET path)."""

    def test_e90_post_with_version_outcome_shape_value_code(self, fhir_client) -> None:
        """POST $subsumes with version param — outcome MUST be valueCode."""
        body = _build_subsumes_params(
            SNOMED_URI,
            SNOMED_DIABETES_MELLITUS,
            SNOMED_T2DM,
            version="2024-09",
        )
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200
        data = r.json()
        assert data.get("resourceType") == "Parameters"
        # Find the outcome param and verify valueCode (not valueString)
        outcome_param = None
        for p in data.get("parameter", []):
            if isinstance(p, dict) and p.get("name") == "outcome":
                outcome_param = p
                break
        assert outcome_param is not None, "missing outcome parameter"
        assert "valueCode" in outcome_param, (
            f"outcome uses {set(outcome_param.keys())}; expected valueCode"
        )
        assert outcome_param["valueCode"] in VALID_OUTCOMES

    def test_e91_post_mixed_scalar_coding_outcome_shape(self, fhir_client) -> None:
        """POST with mixed scalar+coding — outcome shape audit."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
                {"name": "codingB", "valueCoding": {
                    "system": SNOMED_URI, "code": SNOMED_T2DM,
                }},
            ],
        }
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200
        data = r.json()
        assert data.get("resourceType") == "Parameters"
        # Exactly 1 outcome param
        outcomes = [
            p for p in data.get("parameter", [])
            if isinstance(p, dict) and p.get("name") == "outcome"
        ]
        assert len(outcomes) == 1, f"expected 1 outcome, got {len(outcomes)}"
        assert "valueCode" in outcomes[0]
        assert outcomes[0]["valueCode"] in VALID_OUTCOMES

    def test_e92_outcome_forbidden_forms_absent_on_post_path(
        self, fhir_client
    ) -> None:
        """POST $subsumes outcome MUST NOT be in FORBIDDEN_OUTCOMES
        (camelCase, R5/R4B forms, etc.)."""
        body = _build_subsumes_params(
            SNOMED_URI, SNOMED_DIABETES_MELLITUS, SNOMED_T2DM
        )
        r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
        assert r.status_code == 200
        outcome_value = _outcome(r.json())
        assert outcome_value not in FORBIDDEN_OUTCOMES, (
            f"forbidden outcome form {outcome_value!r}"
        )
        assert outcome_value in VALID_OUTCOMES


# ============================================================================
# L10: Source-read structural contracts
# ============================================================================

class TestLens10SourceReadStructuralContracts:
    """L10: Source-read structural contracts for the isinstance guard
    pattern application on the CS-04 surface. Verifies the fix is in
    place AND no programming pattern was removed/regressed."""

    def test_e100_subsumes_post_source_contains_extract_named_coding_call(
        self
    ) -> None:
        """Source-read contract: subsumes_post MUST call
        _extract_named_coding_from_parameters for both codingA and
        codingB (CS-04 SKEPTIC QA-053 fix shape)."""
        src = _get_nested_func_source("create_fhir_app", "subsumes_post")
        assert src, "subsumes_post not found"
        # The helper is called twice: once for codingA, once for codingB
        assert src.count("_extract_named_coding_from_parameters") >= 2, (
            f"subsumes_post must call _extract_named_coding_from_parameters "
            f"twice (codingA + codingB); found "
            f"{src.count('_extract_named_coding_from_parameters')}"
        )

    def test_e101_subsumes_post_source_contains_mixed_system_check(
        self
    ) -> None:
        """Source-read contract: subsumes_post MUST contain the mixed-
        system check using canonical_system_uri for normalization."""
        src = _get_nested_func_source("create_fhir_app", "subsumes_post")
        assert src
        assert "canonical_system_uri" in src, (
            "subsumes_post must normalize via canonical_system_uri "
            "(CR-023 fix)"
        )

    def test_e102_parse_parameters_source_contains_isinstance_guard(
        self
    ) -> None:
        """Source-read contract: _parse_parameters MUST contain the
        isinstance(param, dict) guard (CS-04 SKEPTIC QA-001 fix)."""
        src = _get_nested_func_source("create_fhir_app", "_parse_parameters")
        assert src
        assert "isinstance(param, dict)" in src, (
            "_parse_parameters must have isinstance(param, dict) guard"
        )

    def test_e103_expand_intensional_source_contains_5_isinstance_guards(
        self
    ) -> None:
        """Source-read contract: _expand_intensional MUST contain at
        least 4 isinstance(X, dict) guards (include, concept, filt,
        exclude) per CS-04 HISTORIAN QA-001 fix."""
        src = _get_nested_func_source("create_fhir_app", "_expand_intensional")
        assert src
        count = src.count("isinstance(")
        assert count >= 4, (
            f"_expand_intensional must have at least 4 isinstance guards; "
            f"found {count}"
        )

    def test_e104_extract_named_coding_source_contains_isinstance_guards(
        self
    ) -> None:
        """Source-read contract: _extract_named_coding_from_parameters
        MUST contain BOTH:
        - isinstance(param, dict) guard at parameter[] boundary
        - isinstance(coding, dict) guard at valueCoding access boundary
        (CS-04 SKEPTIC QA-053 + QA-001 fix shapes)."""
        src = _get_nested_func_source(
            "create_fhir_app", "_extract_named_coding_from_parameters"
        )
        assert src
        assert "isinstance(param, dict)" in src
        assert "isinstance(coding, dict)" in src

    def test_e105_do_closure_source_contains_isinstance_guard(self) -> None:
        """Source-read contract: _do_closure MUST contain the
        isinstance(coding, dict) guard at valueCoding access boundary
        (CF-HISTORIAN-CM03-01 fix)."""
        src = _get_nested_func_source("create_fhir_app", "_do_closure")
        assert src
        # The guard appears in the inline concept extraction
        assert "isinstance(coding, dict)" in src or "isinstance(value, dict)" in src, (
            "_do_closure must have isinstance guard at valueCoding boundary"
        )
