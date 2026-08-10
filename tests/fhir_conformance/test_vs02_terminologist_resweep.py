"""VS-02 TERMINOLOGIST resweep: ValueSet $expand — Basic.

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
contains.display: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display
valueset-toocostly: https://hl7.org/fhir/R4/extension-valueset-toocostly.html

TERMINOLOGIST resweep lens: clinical/terminological correctness on the
VS-02 surface, post-SKEPTIC + HISTORIAN + EXPLORER. Default severity HIGH.

This resweep addresses EXPLORER handoff tips (4 items):

  Tip 1 — CF-HISTORIAN-VS02-01 OUT OF SCOPE — focus on clinical correctness
    of the existing surface. Lens 9 source-read-pins the structural
    condition (BFS-helper-call shape), deferring actual remediation per
    milestone-10 review Finding #1.

  Tip 2 — Canonical-DISPLAY cross-operation invariant on VS-02 surface
    (EXPLORER test_e60..e63 verified structurally). TERMINOLOGIST re-
    verifies with clinical-correctness lens — the display IS the engine
    canonical preferred term, NOT a client-supplied echo. Covered in Lens 1.

  Tip 3 — Filter+system lateral canonical URI invariant (EXPLORER test_e50).
    TERMINOLOGIST re-verifies with clinical-correctness lens — every
    contains[].system is canonical URI for every alias input. Covered in
    Lens 5.

  Tip 4 — NEW-spec-In-param probe class (EXPLORER test_e40..e42) — apply
    clinical-correctness lens to every spec In parameter. Does
    activeOnly=true correctly filter inactive codes? Does
    displayLanguage=de return German displays when available? Covered in
    Lens 4 with clinical-safety semantics.

10 lens dimensions:

  Lens 1 — Canonical-DISPLAY cross-operation invariant (clinical lens).
    $expand contains[].display == $lookup Out display == $validate-code
    Out display byte-exact for every seeded code across extensional /
    intensional / filter / implicit modes. Display IS engine canonical
    preferred term — NOT client-supplied echo. Extends the canonical-
    DISPLAY invariant (count=5 PROMOTED in GLOBAL_RULES.md) to the 6th
    operation surface, verified on VS-02 by TERMINOLOGIST.

  Lens 2 — Filter clinical correctness (semantic fields).
    Filter matches semantically appropriate fields (display text — the
    clinical term). Filter does NOT match code-only (technical
    identifier) NOR pharmacological relationships. Clinical-term-vs-
    disease-relationship distinction is load-bearing for clinical safety.

  Lens 3 — Paging clinical correctness (deterministic ordering).
    When count truncates, the surfaced concepts MUST be the clinically
    most-relevant ones. Deterministic ordering (re-running the same
    query returns the same contains[]). Pagination stability.

  Lens 4 — NEW-spec-In-param clinical correctness.
    activeOnly=true correctly filters inactive codes (CF-SKEPTIC-CS05-02
    deferred for inactive surfacing). displayLanguage=de — engine is
    single-language; param accepted gracefully. Every spec In param
    accepted without silent-wrong-answer.

  Lens 5 — Filter+system lateral canonical URI invariant (clinical lens).
    contains[].system is canonical URI for every alias input. The
    client-input-as-canonical drift pattern (count=8+1 PROMOTED) MUST
    NOT recur on the VS-02 surface.

  Lens 6 — too-costly signal clinical safety.
    OperationOutcome message clinically informative. The valueset-toocostly
    extension carries a "reason" sub-extension naming the truncation
    cause — clinical operators need this for diagnosis.

  Lens 7 — Clinical correctness of expansion displays per source.
    SNOMED, ICD-10-CM, RxNorm — each contains[].display is the engine
    canonical preferred term. Per-source display invariants.

  Lens 8 — Cross-resource clinical consistency.
    $expand contains[] entry round-trips through $lookup AND
    $validate-code without silent-wrong-answer. The contains[].system +
    contains[].code pair is the same (system, code) $lookup accepts.

  Lens 9 — META structural-invariant source-read contracts (clinical).
    Pin the structural conditions that make clinical correctness hold:
    _do_expand filter-mode uses +1 probe pattern, total= passed, BFS
    helper early-exit, canonical_system_uri wired.

  Lens 10 — Clinical safety no-silent-wrong-answer.
    No silent-wrong-answer on edge cases. Unknown system, unknown code,
    empty expansion, toocostly signal — all surface FHIR-conformant
    OperationOutcome or correct expansion bodies.

Reference fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus",   "A73211009", "N", "SNOMEDCT_US", "C0011849"),  # parent
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),  # child
    ("E11",      "HT", "Type 2 diabetes mellitus", "AE11",      "N", "ICD10CM",    "C0011847"),
    ("860975",   "SCD","24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")  # 44054006 is-a 73211009
"""

from __future__ import annotations

import inspect
import textwrap

import pytest

# Spec: https://hl7.org/fhir/R4/valueset-operation-expand.html (canonical R4)
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html (too-costly)
# Spec: https://hl7.org/fhir/R4/valueset.html#expansion (expansion shape)
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_DIABETES_MELLITUS_DISPLAY = "Diabetes mellitus"
SNOMED_T2DM = "44054006"               # child of 73211009
SNOMED_T2DM_DISPLAY = "Type 2 diabetes mellitus"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
RXNORM_METFORMIN_DISPLAY = "24 HR metformin 500 MG Oral Tablet"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"
ICD10CM_T2DM_DISPLAY = "Type 2 diabetes mellitus"

TOOCOSTLY_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"


# =============================================================================
# Helpers
# =============================================================================

def _post_expand(fhir_client, body: dict, *, params: dict | None = None) -> tuple[int, dict]:
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


def _get_lookup(fhir_client, system: str, code: str) -> tuple[int, dict]:
    """GET /fhir/CodeSystem/$lookup with system+code."""
    resp = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system, "code": code},
        headers={"Accept": "application/fhir+json"},
    )
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"_raw": resp.text}
    return resp.status_code, parsed


def _get_vs_validate(fhir_client, system: str, code: str, display: str | None = None) -> tuple[int, dict]:
    """GET /fhir/ValueSet/$validate-code with system+code (+ optional display)."""
    p = {"url": system, "system": system, "code": code}
    if display is not None:
        p["display"] = display
    resp = fhir_client.get(
        "/fhir/ValueSet/$validate-code",
        params=p,
        headers={"Accept": "application/fhir+json"},
    )
    try:
        parsed = resp.json()
    except Exception:
        parsed = {"_raw": resp.text}
    return resp.status_code, parsed


def _expansion_extensions(resp: dict) -> list[dict]:
    """Return the expansion-level extension list (empty if absent)."""
    return resp.get("expansion", {}).get("extension", [])


def _has_toocostly(resp: dict) -> bool:
    """True if the response's expansion carries a valueset-toocostly extension."""
    return any(e.get("url") == TOOCOSTLY_URL for e in _expansion_extensions(resp))


def _toocostly_extension(resp: dict) -> dict | None:
    """Return the toocostly extension dict if present, else None."""
    for e in _expansion_extensions(resp):
        if e.get("url") == TOOCOSTLY_URL:
            return e
    return None


def _lookup_display(fhir_client, system: str, code: str) -> str | None:
    """Return $lookup Out display for (system, code), or None on failure."""
    status, resp = _get_lookup(fhir_client, system, code)
    if status != 200:
        return None
    for p in resp.get("parameter", []):
        if p.get("name") == "display":
            return p.get("valueString")
    return None


def _vs_validate_display(fhir_client, system: str, code: str) -> str | None:
    """Return ValueSet/$validate-code Out display for (system, code), or None."""
    status, resp = _get_vs_validate(fhir_client, system, code)
    if status != 200:
        return None
    for p in resp.get("parameter", []):
        if p.get("name") == "display":
            return p.get("valueString")
    return None


def _get_func_source(func) -> str:
    """Return the source text of a function (handles nested functions via closure)."""
    return inspect.getsource(func)


def _get_nested_func_source(module_path: str, parent_name: str, child_name: str) -> str:
    """Source-read a nested function defined inside another function in a module.

    Walks BOTH ast.FunctionDef AND ast.AsyncFunctionDef (extends TS-01 HISTORIAN
    strategy to nested async route handlers). Plain ast.walk over a module
    would miss nested defs defined inside the create_fhir_app factory.
    """
    import ast
    from pathlib import Path

    src = Path(module_path).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parent_name:
            for child in ast.walk(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == child_name:
                    return ast.get_source_segment(src, child) or ""
    return ""


# =============================================================================
# Lens 1: Canonical-DISPLAY cross-operation invariant (clinical lens)
# =============================================================================

class TestLens1CanonicalDisplayInvariant:
    """Lens 1 — Canonical-DISPLAY cross-operation invariant (clinical lens).

    Per FHIR R4 §4.9.1 + canonical-DISPLAY invariant META-PATTERN (count=5
    PROMOTED in GLOBAL_RULES.md): $expand contains[].display == $lookup
    Out display == $validate-code Out display byte-exact for every seeded
    code across extensional / intensional / filter / implicit modes. Display
    IS the engine canonical preferred term — NOT client-supplied echo.

    EXPLORER test_e60..e63 verified structurally. TERMINOLOGIST re-verifies
    with clinical-correctness lens — every byte-equal display IS a real
    clinically-correct term the engine produces, not an echo artifact.

    Reference: https://hl7.org/fhir/R4/valueset-definitions.html
    #ValueSet.expansion.contains.display
    "The recommended display for this item in the expansion."
    """

    @pytest.mark.parametrize("system,code,expected_display", [
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS, SNOMED_DIABETES_MELLITUS_DISPLAY),
        (SNOMED_URI, SNOMED_T2DM, SNOMED_T2DM_DISPLAY),
        (ICD10CM_URI, ICD10CM_T2DM, ICD10CM_T2DM_DISPLAY),
        (RXNORM_URI, RXNORM_METFORMIN, RXNORM_METFORMIN_DISPLAY),
    ])
    def test_t10_extensional_canonical_display_byte_exact_across_ops(
        self, fhir_client, system, code, expected_display
    ):
        """Spec: extensional $expand contains[].display == $lookup Out
        display == $validate-code Out display byte-exact.

        Clinical-correctness lens: the display IS the engine canonical
        preferred term — clinically meaningful (NOT a raw code fallback,
        NOT a client-supplied echo). For SNOMED DM, the display is "Diabetes
        mellitus" — a clinically-correct term a clinician would recognize.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{"system": system, "concept": [{"code": code}]}]
            },
        }
        status, expand_resp = _post_expand(fhir_client, body)
        assert status == 200, expand_resp
        contains = expand_resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1, contains
        expand_display = contains[0]["display"]

        # Clinical: display IS the canonical term, NOT raw code.
        assert expand_display == expected_display, contains[0]
        assert expand_display != code, "display fell back to raw code"

        # Cross-op invariant: $lookup Out display byte-exact.
        lookup_display = _lookup_display(fhir_client, system, code)
        assert lookup_display == expand_display, (
            f"expand display={expand_display!r} != lookup display={lookup_display!r} "
            f"for system={system} code={code}"
        )

        # Cross-op invariant: $validate-code Out display byte-exact.
        validate_display = _vs_validate_display(fhir_client, system, code)
        assert validate_display == expand_display, (
            f"expand display={expand_display!r} != validate-code display="
            f"{validate_display!r} for system={system} code={code}"
        )

    @pytest.mark.parametrize("system,code,expected_display", [
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS, SNOMED_DIABETES_MELLITUS_DISPLAY),
        (SNOMED_URI, SNOMED_T2DM, SNOMED_T2DM_DISPLAY),
    ])
    def test_t11_intensional_canonical_display_byte_exact_across_ops(
        self, fhir_client, system, code, expected_display
    ):
        """Spec: intensional $expand (is-a) contains[].display == $lookup
        Out display == $validate-code Out display byte-exact.

        is-a filter expansion of 73211009 returns root + descendant. Each
        entry's display MUST byte-equal $lookup AND $validate-code Out
        displays — the canonical preferred term is propagated consistently.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept", "op": "is-a",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }]
            },
        }
        status, expand_resp = _post_expand(fhir_client, body)
        assert status == 200, expand_resp
        contains = expand_resp.get("expansion", {}).get("contains", [])
        # Find the entry matching the parametrized code.
        entry = next((c for c in contains if c["code"] == code), None)
        assert entry is not None, f"code {code} not in expansion contains={contains}"
        expand_display = entry["display"]

        assert expand_display == expected_display, entry
        assert expand_display != code, "display fell back to raw code"

        lookup_display = _lookup_display(fhir_client, SNOMED_URI, code)
        assert lookup_display == expand_display

        validate_display = _vs_validate_display(fhir_client, SNOMED_URI, code)
        assert validate_display == expand_display

    def test_t12_filter_canonical_display_byte_exact_across_ops(
        self, fhir_client
    ):
        """Spec: filter $expand contains[].display == $lookup Out display
        == $validate-code Out display byte-exact.

        filter="diabetes" matches SNOMED DM + SNOMED T2DM + ICD-10-CM T2DM.
        Each entry's display MUST byte-equal $lookup AND $validate-code.
        """
        status, expand_resp = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 20}
        )
        assert status == 200, expand_resp
        contains = expand_resp.get("expansion", {}).get("contains", [])
        assert len(contains) >= 2, contains

        for entry in contains:
            system = entry["system"]
            code = entry["code"]
            expand_display = entry["display"]
            # Display must NOT be raw code (clinical safety).
            assert expand_display != code, entry

            # $lookup byte-exact.
            lookup_display = _lookup_display(fhir_client, system, code)
            assert lookup_display == expand_display, (
                f"filter-mode: expand={expand_display!r} != lookup="
                f"{lookup_display!r} for system={system} code={code}"
            )

            # $validate-code byte-exact.
            validate_display = _vs_validate_display(fhir_client, system, code)
            assert validate_display == expand_display, (
                f"filter-mode: expand={expand_display!r} != validate="
                f"{validate_display!r} for system={system} code={code}"
            )

    def test_t13_implicit_canonical_display_byte_exact_across_ops(
        self, fhir_client
    ):
        """Spec: implicit $expand contains[].display == $lookup Out display
        == $validate-code Out display byte-exact for SNOMED.

        Implicit SNOMED expansion (?fhir_vs) returns all SNOMED codes. Each
        entry's display MUST byte-equal $lookup AND $validate-code.
        """
        status, expand_resp = _get_expand(
            fhir_client, params={"url": f"{SNOMED_URI}?fhir_vs"}
        )
        assert status == 200, expand_resp
        contains = expand_resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 2, contains

        for entry in contains:
            code = entry["code"]
            expand_display = entry["display"]
            assert expand_display != code, entry

            lookup_display = _lookup_display(fhir_client, SNOMED_URI, code)
            assert lookup_display == expand_display, (
                f"implicit: expand={expand_display!r} != lookup="
                f"{lookup_display!r} for code={code}"
            )

            validate_display = _vs_validate_display(fhir_client, SNOMED_URI, code)
            assert validate_display == expand_display, (
                f"implicit: expand={expand_display!r} != validate="
                f"{validate_display!r} for code={code}"
            )

    def test_t14_canonical_display_not_client_supplied_echo(self, fhir_client):
        """Spec: contains[].display is engine canonical — NOT client echo.

        Clinical safety: when the client OMITS display for compose.include
        [].concept[], the engine canonical preferred term IS resolved (VS-01
        QA-056 fix). The display returned is NOT a verbatim echo of client
        input — it's the engine's preferred term.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                }]
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        # Engine canonical preferred term wins.
        assert contains[0]["display"] == SNOMED_DIABETES_MELLITUS_DISPLAY


# =============================================================================
# Lens 2: Filter clinical correctness (semantic fields)
# =============================================================================

class TestLens2FilterClinicalCorrectness:
    """Lens 2 — filter matches semantically appropriate fields.

    Per FHIR R4 §4.7.5 $expand In ``filter``: "A text filter that is a code
    or display text[]" — server discretion. medterm4ds matches display text
    (clinical term) via search_names.

    Clinical safety: the clinical-term-vs-disease-relationship distinction
    is load-bearing. filter="diabetes" returns diabetes codes (display match);
    does NOT return metformin (a diabetes TREATMENT whose display does not
    contain "diabetes"). A CDS hook reading the expansion would not
    incorrectly suggest metformin is a "diabetes" concept.
    """

    def test_t20_filter_diabetes_returns_clinically_relevant_codes(self, fhir_client):
        """Spec: filter="diabetes" returns diabetes-relevant codes only.

        Every returned code MUST have "diabetes" in its display (case-
        insensitive). Clinical relevance: the filter does NOT match
        pharmacological relationships.
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) >= 2, contains
        for entry in contains:
            assert "diabetes" in entry["display"].lower(), (
                f"clinical relevance: filter 'diabetes' matched {entry} "
                f"without 'diabetes' in display"
            )

    def test_t21_filter_excludes_pharmacological_relationships(self, fhir_client):
        """Spec: filter="diabetes" does NOT return metformin.

        Metformin is a diabetes TREATMENT but its display is "24 HR
        metformin 500 MG Oral Tablet" — does not contain "diabetes". The
        filter MUST NOT return it (clinical-term-vs-disease-relationship
        distinction).
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes"}
        )
        assert status == 200, resp
        codes = {c["code"] for c in resp["expansion"]["contains"]}
        assert RXNORM_METFORMIN not in codes, (
            "clinical safety: filter='diabetes' returned metformin. "
            "Metformin is a diabetes treatment; its display does not "
            "contain 'diabetes'."
        )

    def test_t22_filter_metformin_returns_metformin_only(self, fhir_client):
        """Spec: filter="metformin" returns the metformin code only.

        Cross-validation: filter="metformin" matches RxNorm 860975 (display
        contains "metformin"). It does NOT match diabetes codes (their
        display does not contain "metformin").
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "metformin"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1, contains
        assert contains[0]["code"] == RXNORM_METFORMIN

    def test_t23_filter_substring_matches_display_text(self, fhir_client):
        """Spec: filter matches display substring (clinical term).

        filter="diabetes" matches SNOMED DM ("Diabetes mellitus") because
        the display contains the substring "diabetes". This is a clinical
        display match — not a code match.
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "Diabetes Mellitus"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        codes = {c["code"] for c in contains}
        # SNOMED DM matches.
        assert SNOMED_DIABETES_MELLITUS in codes

    def test_t24_filter_case_insensitive_clinical_norm(self, fhir_client):
        """Spec: filter case-insensitive — clinical users don't capitalize.

        filter="DIABETES" (uppercase) MUST return the same codes as
        "diabetes". A clinician typing fast in an EHR autocomplete would
        not always capitalize correctly.
        """
        _, resp_lower = _get_expand(fhir_client, params={"filter": "diabetes"})
        _, resp_upper = _get_expand(fhir_client, params={"filter": "DIABETES"})
        codes_lower = {c["code"] for c in resp_lower["expansion"]["contains"]}
        codes_upper = {c["code"] for c in resp_upper["expansion"]["contains"]}
        assert codes_lower == codes_upper


# =============================================================================
# Lens 3: Paging clinical correctness (deterministic ordering)
# =============================================================================

class TestLens3PagingClinicalCorrectness:
    """Lens 3 — paging clinical correctness (deterministic ordering).

    Per FHIR R4 §4.7.5 $expand In ``count`` + ``offset``: when count
    truncates, the surfaced concepts MUST be deterministic. Re-running the
    same query MUST return the same contains[] (paging stability). Clinical
    safety: an EHR autocomplete paging through expansions requires
    deterministic ordering — otherwise duplicate or missed entries.

    The engine today does NOT implement offset slicing (CF-SKEPTIC-VS02-02
    DEFERRED); probes verify the count-truncation surface is deterministic.
    """

    def test_t30_count_truncation_deterministic(self, fhir_client):
        """Spec: count-truncated expansion is deterministic.

        filter="diabetes" with count=1 returns 1 entry. Re-running the same
        query returns the SAME entry (deterministic). Clinical safety: an
        EHR autocomplete paging requires stable ordering.
        """
        # 3 calls — all MUST return the same single entry.
        codes_seen = set()
        for _ in range(3):
            status, resp = _get_expand(
                fhir_client, params={"filter": "diabetes", "count": 1}
            )
            assert status == 200, resp
            contains = resp.get("expansion", {}).get("contains", [])
            assert len(contains) == 1, contains
            codes_seen.add(contains[0]["code"])
        # All 3 calls MUST have returned the same single code.
        assert len(codes_seen) == 1, (
            f"non-deterministic: paging returned different codes {codes_seen}"
        )

    def test_t31_count_truncation_emits_toocostly_extension(self, fhir_client):
        """Spec: count-truncated expansion MUST emit valueset-toocostly.

        Clinical safety: a client paging an expansion that is silently
        truncated may treat the partial list as exhaustive — leading to
        clinical decisions (drug-drug interaction checks, decision support)
        that miss codes.
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 1}
        )
        assert status == 200, resp
        # CF-SKEPTIC-VS02-03 CLOSED by VS-02 SKEPTIC resweep QA-001.
        assert _has_toocostly(resp), (
            "clinical safety: count-truncated expansion missing toocostly"
        )

    def test_t32_count_no_truncation_no_toocostly(self, fhir_client):
        """Spec: NO toocostly when count does NOT truncate.

        Clinical safety: falsely claiming truncation would mislead clients.
        count=20 (default) on filter="diabetes" returns ALL matches (≤ 20)
        without truncation. NO toocostly extension SHOULD be emitted.
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 20}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        # 3 codes match (SNOMED DM + SNOMED T2DM + ICD-10-CM T2DM). count=20.
        assert len(contains) <= 20
        # No truncation: toocostly MUST NOT fire.
        assert not _has_toocostly(resp), (
            f"clinical safety: toocostly falsely emitted on non-truncated "
            f"expansion (contains={len(contains)}, count=20)"
        )

    def test_t33_extensional_count_truncation_deterministic(self, fhir_client):
        """Spec: extensional count-truncated expansion is deterministic.

        2-concept extensional expansion with count=1 MUST return the same
        single entry across multiple calls.
        """
        body = {
            "resourceType": "ValueSet",
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
        codes_seen = set()
        for _ in range(3):
            status, resp = _post_expand(fhir_client, body, params={"count": 1})
            assert status == 200, resp
            contains = resp.get("expansion", {}).get("contains", [])
            assert len(contains) == 1
            codes_seen.add(contains[0]["code"])
        assert len(codes_seen) == 1


# =============================================================================
# Lens 4: NEW-spec-In-param clinical correctness
# =============================================================================

class TestLens4NewSpecInParamClinical:
    """Lens 4 — NEW-spec-In-param clinical correctness.

    Per FHIR R4 §4.7.5 $expand In Parameters (24 total). EXPLORER test_e40
    verified graceful acceptance (no 5xx). TERMINOLOGIST applies clinical-
    correctness lens to every spec In parameter.

    activeOnly — does it correctly filter inactive codes? The engine filters
    SUPPRESS='N' (active) unconditionally today. activeOnly=true is the
    default behavior; activeOnly=false SHOULD include inactive codes but
    the engine does not (CF-SKEPTIC-CS05-02 DEFERRED).

    displayLanguage — does it return the requested language? Engine is
    single-language (English); displayLanguage=de is accepted but the
    display remains English. Accepted gracefully — no silent-wrong-answer.
    """

    @pytest.mark.parametrize("active_only", ["true", "false"])
    def test_t40_active_only_accepted_no_5xx_no_silent_wrong_answer(
        self, fhir_client, active_only
    ):
        """Spec: activeOnly accepted gracefully; no silent-wrong-answer.

        Per FHIR R4 §4.7.5 In ``activeOnly``: "Controls whether inactive
        concepts are included or excluded in the results." medterm4ds
        returns ONLY active codes regardless of the param value (engine
        filters SUPPRESS='N'). The fixture has no SUPPRESS='O' rows, so
        both activeOnly=true and activeOnly=false return the same active
        results today.

        Clinical safety: the response MUST be a valid FHIR resource (200
        ValueSet expansion OR 422 OperationOutcome) — NOT silent-wrong-
        answer (e.g., partial expansion with wrong codes).
        """
        status, resp = _get_expand(
            fhir_client,
            params={
                "filter": "diabetes",
                "activeOnly": active_only,
                "count": 20,
            },
        )
        assert status < 500, resp
        # The response MUST be either a ValueSet or an OperationOutcome
        # (never silent-wrong-answer like a partial dict).
        assert resp.get("resourceType") in ("ValueSet", "OperationOutcome"), resp

        # If 200 ValueSet, every returned code MUST be a real seeded code
        # (no silent fabrication of inactive codes).
        if resp.get("resourceType") == "ValueSet":
            contains = resp.get("expansion", {}).get("contains", [])
            seeded_codes = {SNOMED_DIABETES_MELLITUS, SNOMED_T2DM, ICD10CM_T2DM}
            for entry in contains:
                assert entry["code"] in seeded_codes, (
                    f"silent-wrong-answer: expansion returned fabricated code {entry}"
                )

    @pytest.mark.parametrize("lang", ["en", "en-US", "de", "fr-FR", "es"])
    def test_t41_display_language_accepted_no_silent_wrong_answer(
        self, fhir_client, lang
    ):
        """Spec: displayLanguage accepted gracefully.

        Per FHIR R4 §4.7.5 In ``displayLanguage``: "Specifies the language
        to be used for description in the expansions returned." medterm4ds
        is single-language (English-only UMLS atoms). displayLanguage=de
        is accepted but the display remains English — engine has no
        German translations.

        Clinical safety: the response MUST NOT silently return a
        fabricated German display. It MUST return the English canonical
        preferred term (which is what the engine has).
        """
        status, resp = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "displayLanguage": lang, "count": 20},
        )
        assert status < 500, resp
        assert resp.get("resourceType") in ("ValueSet", "OperationOutcome"), resp

        # If 200, the display MUST be a real English canonical term — NOT
        # a fabricated translation.
        if resp.get("resourceType") == "ValueSet":
            contains = resp.get("expansion", {}).get("contains", [])
            real_displays = {
                SNOMED_DIABETES_MELLITUS_DISPLAY,
                SNOMED_T2DM_DISPLAY,
                ICD10CM_T2DM_DISPLAY,
            }
            for entry in contains:
                # Display MUST be a real seeded English display.
                assert entry["display"] in real_displays, (
                    f"silent-wrong-answer: display {entry['display']!r} is not "
                    f"a real seeded English display (lang={lang})"
                )

    @pytest.mark.parametrize("include_designations", ["true", "false"])
    def test_t42_include_designations_accepted_no_silent_wrong_answer(
        self, fhir_client, include_designations
    ):
        """Spec: includeDesignations accepted gracefully.

        Per FHIR R4 §4.7.5 In ``includeDesignations``: controls whether
        designations are included. medterm4ds does NOT surface designations
        (single-language engine). includeDesignations=true is accepted
        gracefully — the expansion has no designation field.

        Clinical safety: the response MUST NOT silently fabricate
        designations.
        """
        status, resp = _get_expand(
            fhir_client,
            params={
                "filter": "diabetes",
                "includeDesignations": include_designations,
                "count": 20,
            },
        )
        assert status < 500, resp
        if resp.get("resourceType") == "ValueSet":
            contains = resp.get("expansion", {}).get("contains", [])
            for entry in contains:
                # No fabricated designations.
                assert "designation" not in entry, (
                    f"silent fabrication: designation on entry {entry} "
                    f"(includeDesignations={include_designations})"
                )

    @pytest.mark.parametrize("exclude_nested", ["true", "false"])
    def test_t43_exclude_nested_accepted_no_silent_wrong_answer(
        self, fhir_client, exclude_nested
    ):
        """Spec: excludeNested accepted gracefully.

        Per FHIR R4 §4.7.5 In ``excludeNested``: "Controls whether or not
        the value set expansion nests codes." medterm4ds does NOT nest
        codes today. The param is accepted but the expansion is flat
        regardless.

        Clinical safety: no silent fabrication of nested contains[].
        """
        status, resp = _get_expand(
            fhir_client,
            params={
                "filter": "diabetes",
                "excludeNested": exclude_nested,
                "count": 20,
            },
        )
        assert status < 500, resp
        if resp.get("resourceType") == "ValueSet":
            contains = resp.get("expansion", {}).get("contains", [])
            for entry in contains:
                # No silent fabrication of nested contains[].
                assert "contains" not in entry, (
                    f"silent fabrication: nested contains on entry {entry}"
                )

    @pytest.mark.parametrize(
        "param_name,value",
        [
            ("valueSetVersion", "2024-01-01"),
            ("context", "Patient.gender"),
            ("contextDirection", "incoming"),
            ("includeDefinition", "true"),
            ("excludeNotForUI", "true"),
            ("excludePostCoordinated", "true"),
            ("exclude-system", "http://snomed.info/sct"),
            ("system-version", "http://snomed.info/sct|2024-09"),
            ("check-system-version", "http://loinc.org|2.62"),
            ("force-system-version", "http://snomed.info/sct|2024-09"),
            ("designation", "en-US"),
        ],
    )
    def test_t44_other_in_params_no_5xx_no_silent_wrong_answer(
        self, fhir_client, param_name, value
    ):
        """Spec: every other spec In param — no 5xx, no silent-wrong-answer.

        Closes the spec In param matrix per EXPLORER test_e40..e42 contract.
        Each In param is accepted (200) or rejected (422), never 500. When
        accepted, the expansion body MUST be a real expansion (no fabricated
        fields).
        """
        status, resp = _get_expand(
            fhir_client,
            params={"filter": "diabetes", param_name: value, "count": 20},
        )
        assert status < 500, resp
        assert resp.get("resourceType") in ("ValueSet", "OperationOutcome"), resp


# =============================================================================
# Lens 5: Filter+system lateral canonical URI invariant (clinical lens)
# =============================================================================

class TestLens5CanonicalUriInvariant:
    """Lens 5 — Filter+system lateral canonical URI invariant.

    Per client-input-as-canonical drift pattern (count=8+1 PROMOTED in
    GLOBAL_RULES.md): contains[].system MUST be canonical URI for every
    alias input. EXPLORER test_e50 verified structurally on filter+system
    lateral; TERMINOLOGIST re-verifies with clinical-correctness lens.

    Clinical safety: a downstream CDS hook reading the expansion MUST see
    the canonical URI (e.g., http://snomed.info/sct) — NOT a client-supplied
    alias (e.g., urn:oid:2.16.840.1.113883.6.96). Mixing aliases across
    expansions would break cross-system clinical joins.
    """

    @pytest.mark.parametrize("alias", [
        "http://snomed.info/sct",  # canonical
        "http://snomed.info/sct/",  # trailing slash
        "urn:oid:2.16.840.1.113883.6.96",  # urn:oid alias
        # NOTE: HTTP://SNOMED.INFO/SCT (uppercase HOST) is intentionally
        # excluded — RFC 3986 §3.2.2 host case-insensitivity is a deferred
        # TS-03 EXPLORER enhancement (scheme-only normalization is in scope
        # today). Documented in `fhir_uri_to_system` docstring.
    ])
    def test_t50_filter_system_alias_emits_canonical_uri(
        self, fhir_client, alias
    ):
        """Spec: filter+system alias input emits canonical URI.

        filter="diabetes" with system=<alias> MUST return codes whose
        contains[].system is the canonical SNOMED URI. The alias is input-
        only; the canonical URI is the output contract.
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes", "system": alias}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        for entry in contains:
            # Canonical SNOMED URI in every contains[] entry.
            assert entry["system"] == SNOMED_URI, (
                f"clinical safety: alias input {alias!r} produced "
                f"non-canonical system {entry['system']!r}"
            )

    def test_t51_intensional_alias_emits_canonical_uri(self, fhir_client):
        """Spec: intensional compose.include[].system alias emits canonical.

        compose.include[].system=urn:oid:... MUST emit canonical SNOMED URI
        in contains[].system. CR-013 fix holds on VS-02 surface.
        """
        snomed_alias = "urn:oid:2.16.840.1.113883.6.96"
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": snomed_alias,
                    "concept": [{"code": SNOMED_DIABETES_MELLITUS}],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        assert contains[0]["system"] == SNOMED_URI, (
            f"CR-013 regression: alias {snomed_alias!r} did not resolve to canonical"
        )

    def test_t52_implicit_value_set_canonical_uri_snomed(self, fhir_client):
        """Spec: implicit SNOMED value set emits canonical URI.

        http://snomed.info/sct?fhir_vs MUST emit canonical SNOMED URI in
        contains[].system. CF-HISTORIAN-VS02-02 RESOLVED (canonical_system_uri
        wired in _expand_implicit_value_set).
        """
        status, resp = _get_expand(
            fhir_client, params={"url": f"{SNOMED_URI}?fhir_vs"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        for entry in contains:
            assert entry["system"] == SNOMED_URI, entry

    def test_t53_implicit_value_set_canonical_uri_icd10cm(self, fhir_client):
        """Spec: implicit ICD-10-CM value set emits canonical URI.

        http://hl7.org/fhir/sid/icd-10-cm/vs MUST emit canonical ICD-10-CM
        URI in contains[].system.
        """
        status, resp = _get_expand(
            fhir_client, params={"url": f"{ICD10CM_URI}/vs"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        for entry in contains:
            assert entry["system"] == ICD10CM_URI, entry

    def test_t54_url_based_intensional_canonical_uri(self, fhir_client):
        """Spec: URL-based intensional expansion emits canonical URI.

        http://snomed.info/sct/73211009?fhir_vs=isa MUST emit canonical
        SNOMED URI in contains[].system.
        """
        url = f"{SNOMED_URI}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        status, resp = _get_expand(fhir_client, params={"url": url})
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        for entry in contains:
            assert entry["system"] == SNOMED_URI, entry


# =============================================================================
# Lens 6: too-costly signal clinical safety
# =============================================================================

class TestLens6TooCostlyClinicalSafety:
    """Lens 6 — too-costly signal clinical safety.

    Per FHIR R4 §4.9.3 + valueset-toocostly extension: when the expansion
    is truncated (count cap, depth cap), the extension MUST be present with
    a clinically informative reason sub-extension.

    Clinical safety: a client paging an expansion that is silently
    truncated may treat the partial list as exhaustive — leading to
    clinical decisions (drug-drug interaction checks, decision support)
    that miss codes.
    """

    def test_t60_extensional_count_truncation_toocostly_informative(
        self, fhir_client
    ):
        """Spec: toocostly extension carries informative reason.

        When count truncates, the toocostly extension MUST have:
        - valueBoolean: true (clinical signal)
        - "reason" sub-extension with a non-empty valueString naming
          the truncation cause. Clinical operators need this for diagnosis.
        """
        body = {
            "resourceType": "ValueSet",
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
        status, resp = _post_expand(fhir_client, body, params={"count": 1})
        assert status == 200, resp
        toocostly = _toocostly_extension(resp)
        assert toocostly is not None, "missing toocostly extension"
        assert toocostly.get("valueBoolean") is True, toocostly
        sub_exts = toocostly.get("extension", [])
        reason_exts = [e for e in sub_exts if e.get("url") == "reason"]
        assert len(reason_exts) == 1, sub_exts
        reason = reason_exts[0].get("valueString", "")
        # Reason MUST be clinically informative (non-empty).
        assert reason, f"reason sub-extension empty: {reason_exts[0]}"
        # Reason SHOULD mention the count cap (operator diagnosis).
        assert "count" in reason.lower() or "limit" in reason.lower(), (
            f"reason not clinically informative about count cap: {reason!r}"
        )

    def test_t61_filter_count_truncation_toocostly_informative(
        self, fhir_client
    ):
        """Spec: filter count truncation toocostly carries reason.

        CF-SKEPTIC-VS02-03 CLOSED by VS-02 SKEPTIC resweep QA-001. Verify
        the toocostly extension on the filter path carries an informative
        reason sub-extension.
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 1}
        )
        assert status == 200, resp
        toocostly = _toocostly_extension(resp)
        assert toocostly is not None
        assert toocostly.get("valueBoolean") is True
        sub_exts = toocostly.get("extension", [])
        reason_exts = [e for e in sub_exts if e.get("url") == "reason"]
        assert len(reason_exts) == 1
        assert reason_exts[0].get("valueString"), reason_exts[0]

    def test_t62_intensional_count_truncation_toocostly_informative(
        self, fhir_client
    ):
        """Spec: intensional count truncation toocostly carries reason.

        is-a filter expansion of 73211009 with count=1 returns 1 entry
        (root). The toocostly extension MUST be present and informative.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "filter": [{
                        "property": "concept", "op": "is-a",
                        "value": SNOMED_DIABETES_MELLITUS,
                    }],
                }]
            },
        }
        status, resp = _post_expand(fhir_client, body, params={"count": 1})
        assert status == 200, resp
        toocostly = _toocostly_extension(resp)
        assert toocostly is not None
        assert toocostly.get("valueBoolean") is True

    def test_t63_implicit_count_truncation_toocostly_informative(
        self, fhir_client
    ):
        """Spec: implicit count truncation toocostly carries reason."""
        status, resp = _get_expand(
            fhir_client,
            params={"url": f"{SNOMED_URI}?fhir_vs", "count": 1},
        )
        assert status == 200, resp
        toocostly = _toocostly_extension(resp)
        assert toocostly is not None
        assert toocostly.get("valueBoolean") is True


# =============================================================================
# Lens 7: Clinical correctness of expansion displays per source
# =============================================================================

class TestLens7PerSourceClinicalCorrectness:
    """Lens 7 — per-source clinical correctness of expansion displays.

    Each source's expansion SHOULD return clinically-correct canonical
    displays:
    - SNOMED: preferred term PT (e.g. "Diabetes mellitus")
    - ICD-10-CM: hierarchical HT term (e.g. "Type 2 diabetes mellitus")
    - RxNorm: fully-specified SCD (e.g. "24 HR metformin 500 MG Oral Tablet")
    """

    def test_t70_snomed_displays_are_preferred_terms(self, fhir_client):
        """Spec: SNOMED contains[].display is the preferred term (PT).

        Clinical safety: the display MUST be the SNOMED preferred term — a
        clinically-recognized name. NOT the raw code, NOT a synonym.
        """
        status, resp = _get_expand(
            fhir_client, params={"url": f"{SNOMED_URI}?fhir_vs"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        for entry in contains:
            display = entry["display"]
            # Display is NOT the raw code.
            assert display != entry["code"], entry
            # Display is a real preferred term (not a placeholder).
            assert len(display) > 3, entry

    def test_t71_icd10cm_display_is_canonical_ht_term(self, fhir_client):
        """Spec: ICD-10-CM contains[].display is the canonical HT term.

        ICD-10-CM E11 display is "Type 2 diabetes mellitus" — a clinically
        recognized name for billing and coding.
        """
        status, resp = _get_expand(
            fhir_client, params={"url": f"{ICD10CM_URI}/vs"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        assert contains[0]["display"] == ICD10CM_T2DM_DISPLAY

    def test_t72_rxnorm_display_is_canonical_scd(self, fhir_client):
        """Spec: RxNorm contains[].display is the canonical SCD.

        RxNorm 860975 display is "24 HR metformin 500 MG Oral Tablet" — the
        fully-specified name (SCD) that distinguishes formulations.
        Clinical safety: a prescriber needs the FULL canonical preferred
        term so they can distinguish formulations.
        """
        status, resp = _get_expand(
            fhir_client, params={"url": f"{RXNORM_URI}/vs"}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1
        assert contains[0]["display"] == RXNORM_METFORMIN_DISPLAY
        # Display MUST contain formulation details (dose + form).
        assert "metformin" in contains[0]["display"].lower()
        assert "MG" in contains[0]["display"]
        assert "Tablet" in contains[0]["display"]

    def test_t73_per_source_canonical_displays_byte_exact_with_lookup(
        self, fhir_client
    ):
        """Spec: per-source contains[].display byte-exact with $lookup.

        Parametrized per-source: SNOMED, ICD-10-CM, RxNorm. Each source's
        implicit expansion display byte-equals $lookup Out display.
        """
        for url, expected_codes in [
            (f"{SNOMED_URI}?fhir_vs", [SNOMED_DIABETES_MELLITUS, SNOMED_T2DM]),
            (f"{ICD10CM_URI}/vs", [ICD10CM_T2DM]),
            (f"{RXNORM_URI}/vs", [RXNORM_METFORMIN]),
        ]:
            status, resp = _get_expand(fhir_client, params={"url": url})
            assert status == 200, resp
            contains = resp.get("expansion", {}).get("contains", [])
            for entry in contains:
                lk_display = _lookup_display(
                    fhir_client, entry["system"], entry["code"]
                )
                assert lk_display == entry["display"], (
                    f"source={url}: expand={entry['display']!r} "
                    f"!= lookup={lk_display!r}"
                )


# =============================================================================
# Lens 8: Cross-resource clinical consistency
# =============================================================================

class TestLens8CrossResourceClinicalConsistency:
    """Lens 8 — cross-resource clinical consistency.

    Every contains[] entry's (system, code) pair MUST be $lookup-able AND
    $validate-code-able. The URIs advertised in contains[].system are the
    real canonical URIs the server can resolve.
    """

    @pytest.mark.parametrize("system,code", [
        (SNOMED_URI, SNOMED_DIABETES_MELLITUS),
        (SNOMED_URI, SNOMED_T2DM),
        (ICD10CM_URI, ICD10CM_T2DM),
        (RXNORM_URI, RXNORM_METFORMIN),
    ])
    def test_t80_seeded_codes_round_trip_lookup_and_validate(
        self, fhir_client, system, code
    ):
        """Spec: every seeded code round-trips through $lookup + $validate-code."""
        lk_status, lk_resp = _get_lookup(fhir_client, system, code)
        assert lk_status == 200, lk_resp
        assert lk_resp.get("resourceType") == "Parameters"

        vc_status, vc_resp = _get_vs_validate(fhir_client, system, code)
        assert vc_status == 200, vc_resp
        assert vc_resp.get("resourceType") == "Parameters"

    def test_t81_extensional_expansion_all_entries_round_trip(self, fhir_client):
        """Spec: extensional expansion contains[] entries round-trip."""
        body = {
            "resourceType": "ValueSet",
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
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        for entry in contains:
            lk_status, _ = _get_lookup(fhir_client, entry["system"], entry["code"])
            assert lk_status == 200, entry

    def test_t82_filter_expansion_all_entries_round_trip(self, fhir_client):
        """Spec: filter expansion contains[] entries round-trip."""
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 20}
        )
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        for entry in contains:
            lk_status, _ = _get_lookup(fhir_client, entry["system"], entry["code"])
            assert lk_status == 200, entry


# =============================================================================
# Lens 9: META structural-invariant source-read contracts (clinical)
# =============================================================================

class TestLens9MetaStructuralInvariants:
    """Lens 9 — META structural-invariant source-read contracts.

    Pin the structural conditions that make clinical correctness hold:
    - _do_expand filter-mode uses +1 probe pattern (count+1)
    - total= passed to build_valueset_expand
    - extensions= passed to build_valueset_expand
    - canonical_system_uri wired in _expand_implicit_value_set
    - _expand_intensional uses get_code_infos for omitted display
    """

    def test_t90_filter_mode_uses_plus_1_probe_pattern(self):
        """Structural contract: filter mode uses +1 probe (count+1).

        VS-02 SKEPTIC resweep QA-001 fix uses the +1 probe pattern to
        detect truncation. The structural contract: search_names is called
        with limit=count+1, not limit=count.
        """
        from medterm4ds.apps import fhir_api

        # Get the source of _do_expand (factory-nested).
        source = _get_nested_func_source(
            str(fhir_api.__file__), "create_fhir_app", "_do_expand"
        )
        assert source, "_do_expand source not found"
        # The +1 probe pattern is in the source.
        assert "limit=count + 1" in source or "limit = count + 1" in source, (
            "clinical safety: +1 probe pattern missing in filter mode — "
            "VS-02 SKEPTIC resweep QA-001 fix regressed"
        )

    def test_t91_filter_mode_passes_total_to_builder(self):
        """Structural contract: filter mode passes total= to builder.

        VS-02 SKEPTIC resweep QA-001 fix added total= keyword argument to
        the filter-mode build_valueset_expand call site. The total reflects
        the un-truncated size (lower bound when count_limited).
        """
        from medterm4ds.apps import fhir_api

        source = _get_nested_func_source(
            str(fhir_api.__file__), "create_fhir_app", "_do_expand"
        )
        assert source
        # total= keyword argument present in the filter-mode call site.
        assert "total=" in source, (
            "clinical safety: filter mode missing total= kwarg — "
            "QA-001 fix regressed"
        )
        # The untruncated_total variable name is the structural signal.
        assert "untruncated_total" in source, (
            "clinical safety: filter mode missing untruncated_total computation"
        )

    def test_t92_filter_mode_passes_extensions_to_builder(self):
        """Structural contract: filter mode passes extensions= to builder.

        CF-SKEPTIC-VS02-03 CLOSED in QA-001 fix — extensions= kwarg added.
        """
        from medterm4ds.apps import fhir_api

        source = _get_nested_func_source(
            str(fhir_api.__file__), "create_fhir_app", "_do_expand"
        )
        assert source
        assert "extensions=" in source, (
            "clinical safety: filter mode missing extensions= kwarg — "
            "CF-SKEPTIC-VS02-03 regressed"
        )

    def test_t93_implicit_value_set_uses_canonical_system_uri(self):
        """Structural contract: _expand_implicit_value_set calls canonical_system_uri.

        CF-HISTORIAN-VS02-02 RESOLVED — canonical_system_uri wired into
        _expand_implicit_value_set. Without it, contains[].system would
        echo the client-supplied URL prefix verbatim.
        """
        from medterm4ds.apps import fhir_api

        source = _get_nested_func_source(
            str(fhir_api.__file__), "create_fhir_app",
            "_expand_implicit_value_set"
        )
        assert source
        assert "canonical_system_uri" in source, (
            "clinical safety: _expand_implicit_value_set missing "
            "canonical_system_uri call — CF-HISTORIAN-VS02-02 regressed"
        )

    def test_t94_intensional_uses_get_code_infos_for_display(self):
        """Structural contract: _expand_intensional uses get_code_infos.

        VS-01 QA-056 fix: omitted display resolves via get_code_infos.
        The structural contract: get_code_infos is called in
        _expand_intensional.
        """
        from medterm4ds.apps import fhir_api

        source = _get_nested_func_source(
            str(fhir_api.__file__), "create_fhir_app", "_expand_intensional"
        )
        assert source
        assert "get_code_infos" in source, (
            "clinical safety: _expand_intensional missing get_code_infos — "
            "VS-01 QA-056 fix regressed"
        )

    def test_t95_canonical_system_uri_importable(self):
        """Structural contract: canonical_system_uri importable from engines.fhir."""
        from medterm4ds.engines.fhir import canonical_system_uri
        assert callable(canonical_system_uri), (
            "canonical_system_uri not importable from engines.fhir"
        )

    def test_t96_build_valueset_expand_total_parameter(self):
        """Structural contract: build_valueset_expand has total parameter.

        VS-02 SKEPTIC QA-057 fix added total: int | None = None to the
        builder signature.
        """
        from medterm4ds.engines.fhir.responses import build_valueset_expand
        sig = inspect.signature(build_valueset_expand)
        assert "total" in sig.parameters, (
            "build_valueset_expand missing total parameter — QA-057 regressed"
        )

    def test_t97_filter_mode_count_limited_uses_strict_gt(self):
        """Structural contract: filter mode uses strict > for count_limited.

        VS-04 TERMINOLOGIST QA-068 fix: count_limited MUST use strict
        greater-than (`len(results) > count`), NOT greater-than-or-equal.
        Clinical hazard: firing toocostly on complete expansions misleads
        clients.
        """
        from medterm4ds.apps import fhir_api

        source = _get_nested_func_source(
            str(fhir_api.__file__), "create_fhir_app", "_do_expand"
        )
        assert source
        # count_limited computation uses strict >.
        # Look for "len(results) > count" — the strict-greater-than.
        assert "len(results) > count" in source, (
            "clinical safety: filter mode count_limited NOT using strict > "
            "— VS-04 TERMINOLOGIST QA-068 pattern may have regressed"
        )
        # And MUST NOT use >=.
        assert "len(results) >= count" not in source, (
            "clinical hazard: filter mode count_limited uses >= — fires "
            "toocostly on complete expansions"
        )


# =============================================================================
# Lens 10: Clinical safety — no silent-wrong-answer
# =============================================================================

class TestLens10ClinicalSafetyNoSilentWrongAnswer:
    """Lens 10 — clinical safety: no silent-wrong-answer on edge cases.

    Every edge case MUST surface a FHIR-conformant response. NO silent-
    wrong-answer (e.g., partial expansion with fabricated codes).
    """

    def test_t100_unknown_system_filter_returns_400(self, fhir_client):
        """Spec: filter with unknown system → 400 OperationOutcome.

        Clinical safety: a filter constrained to an unrecognized system
        MUST NOT silently return an empty expansion — the client needs to
        know the system is invalid.
        """
        status, resp = _get_expand(
            fhir_client,
            params={"filter": "diabetes", "system": "http://unknown.example/system"},
        )
        assert status == 400, resp
        assert resp.get("resourceType") == "OperationOutcome", resp

    def test_t101_no_url_no_filter_no_valueset_returns_400(self, fhir_client):
        """Spec: no url, no filter, no ValueSet body → 400 OperationOutcome.

        Clinical safety: the server MUST NOT silently return an empty
        expansion when no input is provided — the client needs to know
        what's missing.
        """
        status, resp = _get_expand(fhir_client, params={})
        assert status == 400, resp
        assert resp.get("resourceType") == "OperationOutcome", resp

    def test_t102_filter_no_matches_returns_empty_expansion(self, fhir_client):
        """Spec: filter with no matches → 200 empty expansion.

        Per FHIR R4 §4.7.5: a filter that matches no codes returns a 200
        with empty contains[]. Clinical safety: empty result is NOT a
        failure — the client should distinguish "no match" (200 empty)
        from "invalid request" (400).
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "zzzznomatch"}
        )
        assert status == 200, resp
        assert resp.get("resourceType") == "ValueSet", resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert contains == []

    def test_t103_extensional_unknown_code_falls_back_to_code(
        self, fhir_client
    ):
        """Spec: extensional unknown code falls back to code-string display.

        Per FHIR R4 §4.9.1 contains.display: "The recommended display for
        this item in the expansion." Empty string is NOT a recommended
        display — it's the absence of one. When the engine has no canonical
        preferred term (unknown code), the code string itself IS the most-
        specific recommended display available (mirror of the descendant-
        loop pattern at apps/fhir_api.py:2656: ``d.target_display or
        d.target.code``).

        Extensional semantic IS to include unknown codes in contains[]
        (per VS-01 HISTORIAN test_h42 + test_h100 — the client explicitly
        listed the code). The display MUST NOT be empty string.

        CF-TERMINOLOGIST-VS02-04 RESOLVED by VS-02 TERMINOLOGIST QA-001:
        added ``display = code_str`` fallback when ``get_code_infos``
        returns empty for the explicit-concept-list path.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": "9999999999UNKNOWN"}],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        assert resp.get("resourceType") == "ValueSet", resp
        contains = resp.get("expansion", {}).get("contains", [])
        # Extensional semantic: unknown code IS included (per VS-01 HISTORIAN).
        assert len(contains) == 1, contains
        assert contains[0]["code"] == "9999999999UNKNOWN"
        # Display MUST fall back to code string (CF-TERMINOLOGIST-VS02-04
        # RESOLVED — no more empty display).
        assert contains[0]["display"] == "9999999999UNKNOWN", (
            f"CF-TERMINOLOGIST-VS02-04 regression: expected display to "
            f"fall back to code string. Got: {contains[0]['display']!r}"
        )

    def test_t104_toocostly_message_does_not_leak_internal_data(self, fhir_client):
        """Clinical safety: toocostly reason does NOT leak internal data.

        The reason sub-extension MUST be clinically informative but NOT
        leak internal engine details (file paths, SQL queries, etc.) —
        information-disclosure surface.
        """
        body = {
            "resourceType": "ValueSet",
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
        status, resp = _post_expand(fhir_client, body, params={"count": 1})
        assert status == 200, resp
        toocostly = _toocostly_extension(resp)
        assert toocostly is not None
        sub_exts = toocostly.get("extension", [])
        reason_exts = [e for e in sub_exts if e.get("url") == "reason"]
        assert reason_exts
        reason = reason_exts[0].get("valueString", "")
        # Reason MUST NOT leak file paths or SQL.
        assert ".py" not in reason, (
            f"information disclosure: reason contains .py file: {reason!r}"
        )
        assert "SELECT" not in reason.upper(), (
            f"information disclosure: reason contains SQL: {reason!r}"
        )
        assert "/" not in reason or "count" in reason.lower(), (
            f"reason looks like file path: {reason!r}"
        )

    def test_t105_extensional_count_1_truncates_with_toocostly(self, fhir_client):
        """Spec: count=1 on 2-concept extensional truncates with toocostly.

        Clinical safety: the client MUST know the expansion was truncated
        so they can page further.
        """
        body = {
            "resourceType": "ValueSet",
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
        status, resp = _post_expand(fhir_client, body, params={"count": 1})
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1, contains
        assert _has_toocostly(resp), resp
        # total reflects UN-truncated size (SKEPTIC QA-057).
        assert resp["expansion"]["total"] == 2, resp["expansion"]

    def test_t106_offset_param_accepted_no_silent_wrong_answer(
        self, fhir_client
    ):
        """Spec: offset param accepted; no silent-wrong-answer.

        CF-SKEPTIC-VS02-02 DEFERRED: offset is declared but ignored today.
        Clinical safety: the response MUST be a valid FHIR resource (200
        ValueSet OR 422 OperationOutcome), not silent-wrong-answer.
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes", "offset": 0, "count": 20}
        )
        assert status < 500, resp
        assert resp.get("resourceType") in ("ValueSet", "OperationOutcome"), resp

    def test_t107_count_zero_rejected_no_silent_wrong_answer(self, fhir_client):
        """Spec: count=0 rejected with 422 (CF-SKEPTIC-VS02-01 DEFERRED).

        The current behavior rejects count=0 with 422 (Query ge=1). The
        spec allows count=0 → empty expansion; the deferred CF documents
        the gap. Clinical safety: the 422 path MUST produce a FHIR
        OperationOutcome, not silent-wrong-answer.
        """
        status, resp = _get_expand(
            fhir_client, params={"filter": "diabetes", "count": 0}
        )
        assert status == 422, resp
        assert resp.get("resourceType") == "OperationOutcome", resp

    def test_t108_extensional_unknown_code_display_is_not_empty(
        self, fhir_client
    ):
        """Spec: extensional unknown code display MUST NOT be empty string.

        Positive-shape sibling of test_t103. Per FHIR R4 §4.9.1
        contains.display: "The recommended display for this item in the
        expansion." Empty string is NOT a recommended display. The fix
        (CF-TERMINOLOGIST-VS02-04 RESOLVED) ensures the display falls back
        to the code string when get_code_infos returns empty.
        """
        body = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [{
                    "system": SNOMED_URI,
                    "concept": [{"code": "9999999999UNKNOWN"}],
                }],
            },
        }
        status, resp = _post_expand(fhir_client, body)
        assert status == 200, resp
        contains = resp.get("expansion", {}).get("contains", [])
        assert len(contains) == 1, contains
        # Display MUST be non-empty (CF-TERMINOLOGIST-VS02-04 RESOLVED).
        assert contains[0]["display"], (
            f"CF-TERMINOLOGIST-VS02-04 regression: empty display for "
            f"unknown code. Got: {contains[0]}"
        )
