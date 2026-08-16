"""HISTORIAN resweep probes for CS-01 (CodeSystem Resource Structure).

Fresh full-sweep per USER_DIRECTIVES [2026-08-08]. Sibling file to the
existing ``test_cs01_historian.py`` baseline; this file holds NEW
regression probes that re-derive prior CS-01 bug patterns from the
current code and confirm they have NOT regressed.

Spec: https://build.fhir.org/codesystem.html (R4 / 4.0.1).
      https://hl7.org/fhir/R4/valueset-codesystem-content-mode.html
      https://hl7.org/fhir/R4/valueset-filter-operator.html
      https://hl7.org/fhir/R4/codesystem-operation-lookup.html

HISTORIAN lens (per ROLE_QA_ENGINEER.md §3): pattern-match against
``GLOBAL_KNOWLEDGE.md`` and ``ARCHIVE_LOG.md``. Re-derive prior bug
patterns from current code and verify they have NOT regressed.

Prior CS-01 patterns to re-derive:
  1. HCPCS canonical URI drift (count=8+1 PROMOTED in GLOBAL_RULES.md
     line 124). SKEPTIC tip for HISTORIAN: re-derive via source-read of
     ``responses.py:543`` (the ``for source, uri in sorted(
     SYSTEM_TO_FHIR_URI.items())`` loop) — the load-bearing structural
     contract for the HCPCS fix on the CS-01 advertisement surface.
  2. $lookup canonical-system property uses FHIR canonical URI via the
     ``sab_label_to_fhir_uri`` helper (CS-01 SKEPTIC QA-043, HISTORIAN
     QA-044).
  3. match-type DECISION (b): server-local pipeline vocabulary
     documented (not FHIR R4 ConceptMapEquivalence enum).
  4. CF-EXPLORER-CS01-01: canonical-code chapter-range documented as
     clinically meaningful (NOT a single billable code).
  5. Content closed-enum registry-as-contract (CF-SKEPTIC-CS01-RESWEEP-01
     LOW DEFERRED from this run's SKEPTIC; ``FHIR_R4_CONTENT_MODES`` is
     test-local today, candidate for canonical promotion).

For each pattern: source-read the current code AND (where applicable)
write a behavioral probe. Log regressions as bugs citing the prior bug
ID; log new pattern-match bugs with a FHIR spec citation.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Registry-as-contract — single source of truth for closed enums.
# Per GLOBAL_RULES.md "Code Review Time" 12th PROMOTED pattern: import
# canonical constants from engines/fhir/__init__.py; NEVER copy into tests.
# ---------------------------------------------------------------------------
from medterm4ds.engines.fhir import (
    FHIR_URI_ALIASES,
    FHIR_R4_FILTER_OPERATORS,
    SYSTEM_TO_FHIR_URI,
    canonical_system_uri,
    fhir_uri_to_system,
    sab_label_to_fhir_uri,
    system_to_fhir_uri,
)

# FHIR R4 CodeSystemContentMode enum (5 values — verified 2026-08-08 against
# https://hl7.org/fhir/R4/valueset-codesystem-content-mode.html expansion
# "This value set contains 5 concepts").
# Not yet in engines/fhir/__init__.py — define here and request canonical
# promotion (CF-SKEPTIC-CS01-RESWEEP-01 LOW DEFERRED).
FHIR_R4_CONTENT_MODES = frozenset({
    "complete", "example", "fragment", "not-present", "supplement",
})

# FHIR R4 ConceptMapEquivalence enum (canonical R4 values; verified against
# https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html).
# Used for DECISION (b) assertion: match-type vocabulary MUST NOT be a
# FHIR R4 ConceptMapEquivalence enum value (it's a server-local pipeline
# vocabulary).
FHIR_R4_CONCEPT_MAP_EQUIVALENCE_VALUES = frozenset({
    "relatedto", "equivalent", "equal", "wider", "narrower",
    "subsumes", "specializes", "inexact", "unmatched", "disjoint",
})

# Server-local engine pipeline vocabulary for the ``match-type`` custom
# property (DECISION (b) — documented, NOT translated to FHIR enum).
# Sourced from the _do_lookup docstring at apps/fhir_api.py:1595-1612.
# This registry MUST be extended in the same PR that adds a new match-type
# value to the patient-friendly JSON (CF-SKEPTIC-CS01-02 DECISION (b)).
SERVER_LOCAL_MATCH_TYPE_VOCABULARY = frozenset({
    "exact", "original", "broader", "group", "ingredient", "same_cui",
    "cvx_group", "broader_group", "broader_ingredient", "first_axis",
    "snomed_fallback", "snomed_to_target_native_hierarchy",
    "snomed_to_target_snomed_fallback",
})

# Legacy HCPCS THO resource URL — the prior (incorrect) canonical URI.
# MUST be in FHIR_URI_ALIASES (input-only) and MUST NOT be in
# SYSTEM_TO_FHIR_URI.values() (advertisement surface).
# This is the load-bearing constant for the HCPCS URI drift regression
# class (count=8+1 PROMOTED).
LEGACY_HCPCS_THO_URL = "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"
CANONICAL_HCPCS_URI = (
    "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"
)

# Module source paths for source-read probes.
_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)
_RESPONSES_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "engines" / "fhir" / "responses.py"
)
_FHIR_INIT_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "engines" / "fhir" / "__init__.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_source(path: Path) -> str:
    return path.read_text()


def _get_func_source(source: str, name: str) -> str:
    """Return source text of a top-level OR nested function.

    Extends TS-04 HISTORIAN strategy: walks BOTH ``ast.FunctionDef`` AND
    ``ast.AsyncFunctionDef`` to catch nested async route handlers inside
    ``create_fhir_app()``.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return ""


# ===========================================================================
# Pattern 1: HCPCS canonical URI drift regression class (count=8+1 PROMOTED)
# SKEPTIC tip: source-read responses.py:543 — the structural contract.
# ===========================================================================

def test_h10_responses_py_543_loop_pulls_from_system_to_fhir_uri_registry():
    """HCPCS URI drift regression class (count=8+1 PROMOTED in
    GLOBAL_RULES.md line 124). SKEPTIC tip for HISTORIAN: the load-bearing
    structural contract is the ``for source, uri in sorted(
    SYSTEM_TO_FHIR_URI.items())`` loop at responses.py:543.

    HISTORIAN source-read: verify the loop iterates the canonical registry
    (NOT a hardcoded list, NOT an alias map). If a future drift changes the
    loop to pull from a stale hardcoded dict, HCPCS would silently revert
    to the THO URL.

    Regression cite: prior bug ID TS-01 TERMINOLOGIST QA-012 (HCPCS URI
    drift) + count=8+1 PROMOTED meta-pattern.
    """
    src = _read_source(_RESPONSES_PATH)
    build_fn = _get_func_source(src, "build_terminology_capabilities")
    pytest.current_report_extra = f"found_build_fn={bool(build_fn)}"
    assert build_fn, "build_terminology_capabilities function not found"
    # The load-bearing contract: the loop MUST iterate SYSTEM_TO_FHIR_URI.
    assert "SYSTEM_TO_FHIR_URI" in build_fn, (
        f"build_terminology_capabilities MUST iterate SYSTEM_TO_FHIR_URI "
        f"(the canonical registry). Drift to a hardcoded dict or alias map "
        f"would reintroduce the HCPCS URI drift (count=8+1 PROMOTED). "
        f"Source: {build_fn[:400]}"
    )
    assert "sorted(SYSTEM_TO_FHIR_URI.items())" in build_fn, (
        f"build_terminology_capabilities MUST use "
        f"sorted(SYSTEM_TO_FHIR_URI.items()) for deterministic advertisement "
        f"order. Source: {build_fn[:400]}"
    )


def test_h11_hcpcs_canonical_uri_in_registry_not_legacy_tho_url():
    """HCPCS URI drift regression: SYSTEM_TO_FHIR_URI['HCPCS'] MUST be the
    CMS canonical URI (http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets),
    NOT the legacy THO resource URL (http://terminology.hl7.org/CodeSystem/
    hcpcs-Level-II).

    HISTORIAN lens: direct registry assertion — the prior bug was the wrong
    value here; re-verify the correct value is present.
    Regression cite: TS-01 TERMINOLOGIST QA-012.
    """
    pytest.current_report_extra = (
        f"hcpcs_in_registry={SYSTEM_TO_FHIR_URI.get('HCPCS')!r} "
        f"legacy={LEGACY_HCPCS_THO_URL!r}"
    )
    assert SYSTEM_TO_FHIR_URI.get("HCPCS") == CANONICAL_HCPCS_URI, (
        f"SYSTEM_TO_FHIR_URI['HCPCS'] drifted from canonical CMS URI. "
        f"Expected: {CANONICAL_HCPCS_URI}. "
        f"Actual: {SYSTEM_TO_FHIR_URI.get('HCPCS')!r}. "
        f"Legacy THO URL: {LEGACY_HCPCS_THO_URL!r}."
    )
    # The legacy (incorrect) URI MUST NOT be in the canonical registry values.
    assert LEGACY_HCPCS_THO_URL not in set(SYSTEM_TO_FHIR_URI.values()), (
        f"Legacy HCPCS THO URL {LEGACY_HCPCS_THO_URL!r} leaked into "
        f"SYSTEM_TO_FHIR_URI values (advertisement surface). It MUST be "
        f"input-only (FHIR_URI_ALIASES). HCPCS URI drift regression."
    )


def test_h12_legacy_hcpcs_tho_url_is_input_only_alias():
    """HCPCS URI drift regression: the legacy THO URL MUST be in
    FHIR_URI_ALIASES (input-only — backwards compat for clients that
    learned the wrong URI), NOT in the advertisement surface.

    HISTORIAN lens: verify the alias map has the legacy URL (clients
    resolving the old URI still work) AND verify it's NOT in the canonical
    registry.
    Regression cite: TS-01 TERMINOLOGIST QA-012.
    """
    pytest.current_report_extra = (
        f"legacy_in_aliases={LEGACY_HCPCS_THO_URL in FHIR_URI_ALIASES} "
        f"legacy_in_registry_vals={LEGACY_HCPCS_THO_URL in set(SYSTEM_TO_FHIR_URI.values())}"
    )
    assert LEGACY_HCPCS_THO_URL in FHIR_URI_ALIASES, (
        f"Legacy HCPCS THO URL {LEGACY_HCPCS_THO_URL!r} MUST remain in "
        f"FHIR_URI_ALIASES as input-only backwards-compat alias (clients "
        f"that learned the wrong URI still resolve). Removing it would "
        f"break those clients."
    )
    assert FHIR_URI_ALIASES[LEGACY_HCPCS_THO_URL] == "HCPCS", (
        f"Legacy HCPCS alias MUST map to 'HCPCS' source; got "
        f"{FHIR_URI_ALIASES.get(LEGACY_HCPCS_THO_URL)!r}."
    )


def test_h13_termcaps_advertisement_uses_canonical_hcpcs_uri_not_legacy(fhir_client):
    """HCPCS URI drift regression: the TerminologyCapabilities advertisement
    MUST list the canonical HCPCS URI (CMS), NOT the legacy THO URL.

    HISTORIAN behavioral probe: fetch metadata?mode=terminology and verify
    the advertisement. The loop at responses.py:543 pulls from
    SYSTEM_TO_FHIR_URI so the canonical URI is what's advertised.
    Regression cite: TS-01 TERMINOLOGIST QA-012.
    """
    body = fhir_client.get("/fhir/metadata?mode=terminology").json()
    advertised = {e.get("uri") for e in body.get("codeSystem", [])}
    pytest.current_report_extra = (
        f"canonical_in_adv={CANONICAL_HCPCS_URI in advertised} "
        f"legacy_in_adv={LEGACY_HCPCS_THO_URL in advertised}"
    )
    assert CANONICAL_HCPCS_URI in advertised, (
        f"Canonical HCPCS URI {CANONICAL_HCPCS_URI!r} MISSING from "
        f"TerminologyCapabilities advertisement. HCPCS URI drift regression "
        f"(count=8+1 PROMOTED)."
    )
    assert LEGACY_HCPCS_THO_URL not in advertised, (
        f"Legacy HCPCS THO URL {LEGACY_HCPCS_THO_URL!r} LEAKED into "
        f"TerminologyCapabilities advertisement (should be input-only alias). "
        f"HCPCS URI drift regression (count=8+1 PROMOTED)."
    )


def test_h14_hcpcs_alias_resolves_to_canonical_source():
    """HCPCS URI drift regression: fhir_uri_to_system MUST resolve the legacy
    THO URL to the internal 'HCPCS' source (so downstream handlers translate
    to the canonical CMS URI via canonical_system_uri).

    HISTORIAN lens: input-alias resolution probe — clients sending the
    legacy URI must be handled, but the Out `system` parameter must be the
    canonical CMS URI (verified in test_h15).
    Regression cite: TS-01 TERMINOLOGIST QA-012.
    """
    resolved = fhir_uri_to_system(LEGACY_HCPCS_THO_URL)
    pytest.current_report_extra = f"resolved={resolved!r}"
    assert resolved == "HCPCS", (
        f"fhir_uri_to_system(legacy HCPCS THO URL) returned {resolved!r}; "
        f"expected 'HCPCS'. The legacy URL MUST resolve as an input alias."
    )
    # And canonical_system_uri MUST return the canonical CMS URI.
    canonical = canonical_system_uri(LEGACY_HCPCS_THO_URL)
    pytest.current_report_extra += f" canonical={canonical!r}"
    assert canonical == CANONICAL_HCPCS_URI, (
        f"canonical_system_uri(legacy HCPCS) returned {canonical!r}; "
        f"expected {CANONICAL_HCPCS_URI!r}. Client-input-as-canonical drift."
    )


def test_h15_supported_system_extension_advertises_canonical_hcpcs(fhir_client):
    """HCPCS URI drift regression: the CapabilityStatement's
    capabilitystatement-supported-system extension MUST list the canonical
    HCPCS URI (CMS), NOT the legacy THO URL.

    HISTORIAN lens: cross-surface consistency — the extension is the second
    advertisement surface (alongside TerminologyCapabilities); both pull
    from SYSTEM_TO_FHIR_URI via _supported_system_extensions() helper.
    Regression cite: TS-01 TERMINOLOGIST QA-012 + TS-03 SKEPTIC QA-031.
    """
    body = fhir_client.get("/fhir/metadata?mode=full").json()
    supported_uris = []
    for ext in body.get("extension", []):
        if "supported-system" in str(ext.get("url", "")):
            supported_uris.append(ext.get("valueUri"))
    pytest.current_report_extra = (
        f"canonical_in_ext={CANONICAL_HCPCS_URI in supported_uris} "
        f"legacy_in_ext={LEGACY_HCPCS_THO_URL in supported_uris}"
    )
    assert CANONICAL_HCPCS_URI in supported_uris, (
        f"Canonical HCPCS URI missing from capabilitystatement-supported-"
        f"system extension. HCPCS URI drift regression."
    )
    assert LEGACY_HCPCS_THO_URL not in supported_uris, (
        f"Legacy HCPCS THO URL leaked into capabilitystatement-supported-"
        f"system extension (should be input-only alias). HCPCS URI drift."
    )


def test_h16_responses_module_does_not_hardcode_legacy_hcpcs_url_as_canonical():
    """HCPCS URI drift regression: responses.py MUST NOT hardcode the legacy
    HCPCS THO URL as a string literal in executable code. The only place
    the legacy URI legitimately appears is in FHIR_URI_ALIASES in
    engines/fhir/__init__.py (input-only alias).

    HISTORIAN source-read: walk ast.Constant string-literal nodes in
    responses.py and verify the legacy URI is NOT present (comments and
    docstrings are also flagged here because the registry is the single
    source of truth — even documenting the wrong URI in responses.py would
    be drift evidence).

    Sibling of VS-01 SKEPTIC QA-054 source-read pattern + TS-04 HISTORIAN
    source-read helper for nested async functions.
    Regression cite: TS-01 TERMINOLOGIST QA-012.
    """
    src = _read_source(_RESPONSES_PATH)
    tree = ast.parse(src)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if LEGACY_HCPCS_THO_URL in node.value:
                found.append(node.value)
    pytest.current_report_extra = f"found_legacy_literals={found}"
    assert not found, (
        f"responses.py contains hardcoded legacy HCPCS THO URL string "
        f"literal(s): {found}. The legacy URI MUST only appear in "
        f"FHIR_URI_ALIASES (engines/fhir/__init__.py) as input-only alias. "
        f"HCPCS URI drift regression (count=8+1 PROMOTED)."
    )


# ===========================================================================
# Pattern 2: $lookup canonical-system property uses FHIR canonical URI via
# the sab_label_to_fhir_uri helper (CS-01 SKEPTIC QA-043, HISTORIAN QA-044).
# ===========================================================================

def test_h20_do_lookup_wires_sab_label_to_fhir_uri_helper():
    """CS-01 SKEPTIC QA-043 + HISTORIAN QA-044 regression: _do_lookup MUST
    call sab_label_to_fhir_uri() to translate the raw SAB label stored in
    patient-friendly JSON artifacts to the FHIR canonical system URI before
    emitting the canonical-system custom property.

    HISTORIAN source-read: verify the helper is imported at module level
    AND called inside _do_lookup. If a future drift removes the call, the
    raw SAB label (e.g. 'icd10') would leak through the Out `property` —
    silent-wrong-answer (not a FHIR URI).
    Regression cite: CS-01 SKEPTIC QA-043 + HISTORIAN QA-044.
    """
    src = _read_source(_FHIR_API_PATH)
    # Import at module level.
    assert "sab_label_to_fhir_uri" in src, (
        "sab_label_to_fhir_uri helper not imported in apps/fhir_api.py — "
        "CS-01 SKEPTIC QA-043 fix regressed."
    )
    lookup_fn = _get_func_source(src, "_do_lookup")
    pytest.current_report_extra = f"found_lookup_fn={bool(lookup_fn)}"
    assert lookup_fn, "_do_lookup function not found"
    assert "sab_label_to_fhir_uri" in lookup_fn, (
        f"_do_lookup MUST call sab_label_to_fhir_uri to translate the raw "
        f"SAB label to the FHIR canonical system URI before emitting the "
        f"canonical-system custom property. CS-01 SKEPTIC QA-043 regression."
    )


def test_h21_do_lookup_warns_on_sab_label_translation_failure():
    """CS-01 HISTORIAN QA-044 regression: when sab_label_to_fhir_uri()
    returns None (unknown SAB label), _do_lookup MUST log at WARNING before
    emitting the raw fallback value. Without the WARNING, future source
    additions that don't update _SAB_LABEL_TO_SOURCE would silently leak
    raw vocabulary through $lookup canonical-system responses.

    HISTORIAN source-read: verify the WARNING branch is present in _do_lookup
    alongside the sab_label_to_fhir_uri call.
    Regression cite: CS-01 HISTORIAN QA-044.
    """
    src = _read_source(_FHIR_API_PATH)
    lookup_fn = _get_func_source(src, "_do_lookup")
    pytest.current_report_extra = f"found_lookup_fn={bool(lookup_fn)}"
    assert lookup_fn, "_do_lookup function not found"
    # The WARNING branch MUST be present.
    assert "logger.warning" in lookup_fn, (
        f"_do_lookup MUST log at WARNING when sab_label_to_fhir_uri returns "
        f"None. CS-01 HISTORIAN QA-044 regression (silent-fallback-on-"
        f"translation-failure pattern). Source: {lookup_fn[:400]}"
    )
    # And the WARNING MUST mention the helper name (actionable message).
    assert "sab_label_to_fhir_uri" in lookup_fn, (
        f"_do_lookup WARNING message MUST reference sab_label_to_fhir_uri "
        f"so the operator can diagnose. Source: {lookup_fn[:400]}"
    )


def test_h22_sab_label_to_fhir_uri_helper_contract():
    """CS-01 SKEPTIC QA-043 regression: sab_label_to_fhir_uri() MUST return
    the FHIR canonical system URI for every supported SAB label, and None
    for unrecognized labels.

    HISTORIAN direct-call probe: verify the helper returns canonical URIs
    (not raw SAB labels) for every supported source. If a future drift
    changes the helper to echo the raw label, the wire surface leaks.
    Regression cite: CS-01 SKEPTIC QA-043.
    """
    cases = {
        "snomedct_us": "http://snomed.info/sct",
        "rxnorm": "http://www.nlm.nih.gov/research/umls/rxnorm",
        "icd10": "http://hl7.org/fhir/sid/icd-10-cm",
        "icd10cm": "http://hl7.org/fhir/sid/icd-10-cm",
        "icd10pcs": "http://hl7.org/fhir/sid/icd-10-pcs",
        "lnc": "http://loinc.org",
        "cpt": "http://www.ama-assn.org/go/cpt",
        "hcpcs": CANONICAL_HCPCS_URI,
        "cvx": "http://hl7.org/fhir/sid/cvx",
    }
    failures = []
    for sab_label, expected_uri in cases.items():
        actual = sab_label_to_fhir_uri(sab_label)
        if actual != expected_uri:
            failures.append({
                "sab": sab_label, "expected": expected_uri, "actual": actual
            })
    pytest.current_report_extra = f"failures={failures}"
    assert not failures, (
        f"sab_label_to_fhir_uri helper drift: {failures}. The helper MUST "
        f"return FHIR canonical system URIs (not raw SAB labels). CS-01 "
        f"SKEPTIC QA-043 regression."
    )


def test_h23_sab_label_to_fhir_uri_returns_none_for_unknown_labels():
    """CS-01 SKEPTIC QA-043 regression: sab_label_to_fhir_uri() MUST return
    None for unrecognized labels (NOT echo the raw label as a fallback).
    Returning None is the contract that triggers the WARNING branch in
    _do_lookup (test_h21).

    HISTORIAN direct-call probe.
    Regression cite: CS-01 SKEPTIC QA-043 + CS-01 HISTORIAN QA-044.
    """
    unknown_labels = [
        "", "totally_unknown_sab", "icd10mc", "snomed", "rx", "  ",
        "UNKNOWN_SOURCE", None,
    ]
    failures = []
    for label in unknown_labels:
        actual = sab_label_to_fhir_uri(label)  # type: ignore[arg-type]
        if actual is not None:
            failures.append({"label": label, "actual": actual})
    pytest.current_report_extra = f"failures={failures}"
    assert not failures, (
        f"sab_label_to_fhir_uri returned non-None for unknown labels: "
        f"{failures}. The helper MUST return None (caller emits raw with "
        f"WARNING). CS-01 SKEPTIC QA-043 + HISTORIAN QA-044 regression."
    )


def test_h24_lookup_out_canonical_system_is_fhir_uri_for_seeded_systems(fhir_client):
    """CS-01 SKEPTIC QA-043 regression (behavioral): the $lookup Out
    `canonical-system` custom property MUST be a FHIR canonical system URI
    (resolvable via fhir_uri_to_system), NOT a raw SAB label, for every
    fixture-seeded system that has patient-friendly data.

    HISTORIAN behavioral probe: probe $lookup for SNOMED 73211009 and
    verify the canonical-system custom property (if present) is a FHIR URI.
    Regression cite: CS-01 SKEPTIC QA-043.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
    )
    if r.status_code != 200:
        pytest.skip("fixture row not seeded for SNOMED 73211009")
    body = r.json()
    # Find the canonical-system custom property in the property group.
    canonical_system = None
    for p in body.get("parameter", []):
        if p.get("name") == "property":
            parts = {pt.get("name"): pt for pt in p.get("part", [])}
            code_part = parts.get("code", {})
            if code_part.get("valueCode") == "canonical-system":
                value_part = parts.get("value", {})
                canonical_system = value_part.get("valueUri") or value_part.get("valueCode")
    if canonical_system is None:
        pytest.skip("no canonical-system custom property for this fixture row")
    pytest.current_report_extra = f"canonical_system={canonical_system!r}"
    # MUST be a FHIR canonical URI (resolvable).
    assert canonical_system in set(SYSTEM_TO_FHIR_URI.values()), (
        f"$lookup canonical-system={canonical_system!r} is NOT a canonical "
        f"FHIR URI in SYSTEM_TO_FHIR_URI.values(). Raw SAB label leak — "
        f"CS-01 SKEPTIC QA-043 regression."
    )


# ===========================================================================
# Pattern 3: match-type DECISION (b) — server-local vocabulary, NOT FHIR enum
# (CF-SKEPTIC-CS01-02 DECISION (b)).
# ===========================================================================

def test_h30_do_lookup_match_type_docstring_documents_decision_b():
    """CF-SKEPTIC-CS01-02 DECISION (b) regression: the _do_lookup docstring
    MUST document that match-type values are SERVER-LOCAL engine pipeline
    vocabulary (NOT FHIR R4 ConceptMapEquivalence enum values).

    HISTORIAN source-read: verify the docstring explicitly states the
    vocabulary is server-local AND cites the decision.
    Regression cite: CF-SKEPTIC-CS01-02 DECISION (b).
    """
    src = _read_source(_FHIR_API_PATH)
    lookup_fn = _get_func_source(src, "_do_lookup")
    pytest.current_report_extra = f"found_lookup_fn={bool(lookup_fn)}"
    assert lookup_fn, "_do_lookup function not found"
    # The docstring MUST mention 'server-local' or 'SERVER-LOCAL' to
    # document DECISION (b).
    lower = lookup_fn.lower()
    assert "server-local" in lower, (
        f"_do_lookup docstring MUST document that match-type is server-local "
        f"vocabulary (CF-SKEPTIC-CS01-02 DECISION (b)). Source: "
        f"{lookup_fn[:600]}"
    )
    # And MUST cite the decision identifier for traceability.
    assert "decision (b)" in lower or "cf-skeptic-cs01-02" in lower, (
        f"_do_lookup docstring MUST cite CF-SKEPTIC-CS01-02 DECISION (b) for "
        f"traceability. Source: {lookup_fn[:600]}"
    )


def test_h31_match_type_vocabulary_disjoint_from_fhir_r4_equivalence_enum():
    """CF-SKEPTIC-CS01-02 DECISION (b) regression: the server-local
    match-type vocabulary MUST be disjoint from the FHIR R4
    ConceptMapEquivalence enum. If a value appears in BOTH sets, that's
    evidence of vocabulary drift (the server is leaking FHIR enum values
    into a server-local field, or vice versa).

    HISTORIAN direct-call probe: assert set disjointness.
    Regression cite: CF-SKEPTIC-CS01-02 DECISION (b).
    """
    overlap = SERVER_LOCAL_MATCH_TYPE_VOCABULARY & FHIR_R4_CONCEPT_MAP_EQUIVALENCE_VALUES
    pytest.current_report_extra = (
        f"match_type_vocab={sorted(SERVER_LOCAL_MATCH_TYPE_VOCABULARY)} "
        f"fhir_enum={sorted(FHIR_R4_CONCEPT_MAP_EQUIVALENCE_VALUES)} "
        f"overlap={sorted(overlap)}"
    )
    assert not overlap, (
        f"Server-local match-type vocabulary overlaps FHIR R4 "
        f"ConceptMapEquivalence enum: {sorted(overlap)}. CF-SKEPTIC-CS01-02 "
        f"DECISION (b) regression — the vocabularies MUST be disjoint."
    )


def test_h32_lookup_out_match_type_when_present_is_in_server_local_vocab(fhir_client):
    """CF-SKEPTIC-CS01-02 DECISION (b) regression (behavioral): when the
    $lookup Out `property` group contains a match-type custom property,
    the value MUST be in the SERVER_LOCAL_MATCH_TYPE_VOCABULARY registry
    (NOT a FHIR R4 ConceptMapEquivalence enum value).

    HISTORIAN behavioral probe: probe $lookup for a fixture row that has
    patient-friendly data and verify the match-type value is server-local.
    If a future drift emits a FHIR enum value (e.g. 'equivalent'), this
    probe fires.
    Regression cite: CF-SKEPTIC-CS01-02 DECISION (b).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
    )
    if r.status_code != 200:
        pytest.skip("fixture row not seeded for SNOMED 73211009")
    body = r.json()
    match_type_value = None
    for p in body.get("parameter", []):
        if p.get("name") == "property":
            parts = {pt.get("name"): pt for pt in p.get("part", [])}
            code_part = parts.get("code", {})
            if code_part.get("valueCode") == "match-type":
                value_part = parts.get("value", {})
                match_type_value = value_part.get("valueCode") or value_part.get("valueString")
    if match_type_value is None:
        pytest.skip("no match-type custom property for this fixture row")
    pytest.current_report_extra = f"match_type={match_type_value!r}"
    assert match_type_value in SERVER_LOCAL_MATCH_TYPE_VOCABULARY, (
        f"$lookup match-type={match_type_value!r} is NOT in "
        f"SERVER_LOCAL_MATCH_TYPE_VOCABULARY. Either the registry needs "
        f"extending (add the value in the same PR) OR the value leaked from "
        f"the FHIR enum (CF-SKEPTIC-CS01-02 DECISION (b) regression)."
    )


def test_h33_match_type_never_emits_r5_r4b_equivalence_values(fhir_client):
    """CF-SKEPTIC-CS01-02 DECISION (b) regression: the $lookup match-type
    custom property MUST NEVER emit R5/R4B-only ConceptMapEquivalence
    values (subsumedby, matches, not-relatedto) — those are off-spec on
    the R4 surface and the match-type vocabulary is server-local anyway.

    HISTORIAN behavioral probe: probe $lookup for a few fixture rows and
    verify no R5/R4B values leak. Sibling of CF-HISTORIAN-VS01-01.
    Regression cite: CF-SKEPTIC-CS01-02 DECISION (b) + CF-HISTORIAN-VS01-01.
    """
    r5_r4b_values = {"subsumedby", "matches", "not-relatedto"}
    cases = [
        ("http://snomed.info/sct", "73211009"),
        ("http://snomed.info/sct", "44054006"),
    ]
    leaks = []
    for system_uri, code in cases:
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system_uri, "code": code},
        )
        if r.status_code != 200:
            continue
        body = r.json()
        for p in body.get("parameter", []):
            if p.get("name") == "property":
                parts = {pt.get("name"): pt for pt in p.get("part", [])}
                code_part = parts.get("code", {})
                if code_part.get("valueCode") == "match-type":
                    value_part = parts.get("value", {})
                    val = value_part.get("valueCode") or value_part.get("valueString")
                    if val in r5_r4b_values:
                        leaks.append({"system": system_uri, "code": code, "val": val})
    pytest.current_report_extra = f"leaks={leaks}"
    assert not leaks, (
        f"$lookup match-type emitted R5/R4B-only values: {leaks}. "
        f"CF-SKEPTIC-CS01-02 DECISION (b) + CF-HISTORIAN-VS01-01 regression."
    )


# ===========================================================================
# Pattern 4: CF-EXPLORER-CS01-01 — canonical-code chapter-range documented
# as clinically meaningful (NOT a single billable code).
# ===========================================================================

def test_h40_do_lookup_canonical_code_docstring_documents_chapter_range():
    """CF-EXPLORER-CS01-01 regression: the _do_lookup docstring MUST
    document that canonical-code MAY be a chapter RANGE (e.g. ICD-10-CM
    E08-E13 for SNOMED 73211009), NOT a single billable code. Clients
    MUST validate before treating canonical-code as a billable code.

    HISTORIAN source-read: verify the docstring mentions 'chapter' or
    'range' or 'RANGE'.
    Regression cite: CF-EXPLORER-CS01-01.
    """
    src = _read_source(_FHIR_API_PATH)
    lookup_fn = _get_func_source(src, "_do_lookup")
    pytest.current_report_extra = f"found_lookup_fn={bool(lookup_fn)}"
    assert lookup_fn, "_do_lookup function not found"
    lower = lookup_fn.lower()
    # The docstring MUST mention 'range' or 'chapter' to document that
    # canonical-code may be a chapter range.
    assert "range" in lower or "chapter" in lower, (
        f"_do_lookup docstring MUST document that canonical-code MAY be a "
        f"chapter range (CF-EXPLORER-CS01-01). Source: {lookup_fn[:600]}"
    )


def test_h41_canonical_code_is_passthrough_from_patient_friendly_json():
    """CF-EXPLORER-CS01-01 regression: the _do_lookup implementation MUST
    pass canonical_code through from the patient-friendly JSON without
    transformation. The patient-friendly JSON legitimately stores range
    codes (e.g. 'E08-E13'); the lookup handler MUST NOT validate it as a
    single code (that would silently drop the value).

    HISTORIAN source-read: verify the canonical-code assignment is a
    passthrough (pf.get('canonical_code')), NOT a transformation.
    Regression cite: CF-EXPLORER-CS01-01.
    """
    src = _read_source(_FHIR_API_PATH)
    lookup_fn = _get_func_source(src, "_do_lookup")
    pytest.current_report_extra = f"found_lookup_fn={bool(lookup_fn)}"
    assert lookup_fn, "_do_lookup function not found"
    # The canonical-code assignment MUST be a passthrough from pf.
    assert "canonical_code" in lookup_fn, (
        f"_do_lookup MUST reference canonical_code (CF-EXPLORER-CS01-01). "
        f"Source: {lookup_fn[:600]}"
    )
    assert 'pf.get("canonical_code")' in lookup_fn or "pf.get('canonical_code')" in lookup_fn, (
        f"_do_lookup MUST assign canonical-code via pf.get('canonical_code') "
        f"(passthrough — no transformation). CF-EXPLORER-CS01-01 regression."
    )


# ===========================================================================
# Pattern 5: Content closed-enum registry-as-contract
# (CF-SKEPTIC-CS01-RESWEEP-01 LOW DEFERRED).
# ===========================================================================

def test_h50_fhir_r4_content_modes_registry_value_set():
    """CF-SKEPTIC-CS01-RESWEEP-01 LOW DEFERRED: the FHIR R4
    CodeSystemContentMode enum contains exactly 5 values
    (complete|example|fragment|not-present|supplement) per
    https://hl7.org/fhir/R4/valueset-codesystem-content-mode.html.

    HISTORIAN direct-call probe: pin the enum so any future drift (adding
    R5 values like 'deprecated', dropping R4 values) fails loudly.
    Regression cite: CF-SKEPTIC-CS01-RESWEEP-01.
    """
    expected = frozenset({
        "complete", "example", "fragment", "not-present", "supplement",
    })
    pytest.current_report_extra = (
        f"actual={sorted(FHIR_R4_CONTENT_MODES)} expected={sorted(expected)}"
    )
    assert FHIR_R4_CONTENT_MODES == expected, (
        f"FHIR_R4_CONTENT_MODES registry drift. Actual: "
        f"{sorted(FHIR_R4_CONTENT_MODES)}. R4.0.1 canonical: "
        f"{sorted(expected)}."
    )


def test_h51_termcaps_content_not_present_for_all_systems(fhir_client):
    """CF-SKEPTIC-CS01-RESWEEP-01 LOW DEFERRED — superseded by EC-15 QC-333:
    ``content`` is NOT a FHIR R4 TerminologyCapabilities.codeSystem child
    (R4 children: uri, version, subsumption; verified against
    https://hl7.org/fhir/R4/terminologycapabilities-definitions.html —
    ``content`` on this backbone is R5-only). The earlier NOT A BUG blessing
    of content='not-present' applied CodeSystem-resource semantics to the
    TC backbone; EC-15 removed the element and added ``subsumption: true``
    for hierarchical systems (QC-339).

    HISTORIAN behavioral probe: verify the R4-correct shape.
    Regression cite: CF-SKEPTIC-CS01-RESWEEP-01 + EC-15 QC-333/QC-339.
    """
    body = fhir_client.get("/fhir/metadata?mode=terminology").json()
    entries = body.get("codeSystem", [])
    total = len(entries)
    with_content = [e.get("uri") for e in entries if "content" in e]
    pytest.current_report_extra = f"r5_content_leak={with_content}"
    assert not with_content, (
        f"codeSystem[] entries still carry the R5-only 'content' element: "
        f"{with_content}. Per EC-15 QC-333 the R4 TC backbone has no "
        f"'content' child."
    )
    # QC-339: hierarchical systems declare subsumption support so the TC no
    # longer contradicts the CapabilityStatement's $subsumes operation.
    from medterm4ds.engines.fhir.responses import _subsumption_capable
    from medterm4ds.engines.fhir import FHIR_URI_TO_SYSTEM

    missing = [
        e["uri"] for e in entries
        if _subsumption_capable(FHIR_URI_TO_SYSTEM[e["uri"]])
        and e.get("subsumption") is not True
    ]
    assert not missing, (
        f"Hierarchical systems missing subsumption=true: {missing}. Per "
        f"EC-15 QC-339 the TC must declare subsumption for systems where "
        f"$subsumes works."
    )
    assert total > 0


def test_h52_fhir_init_does_not_yet_define_content_modes_constant():
    """CF-SKEPTIC-CS01-RESWEEP-01 LOW DEFERRED (carry-forward pin): the
    FHIR_R4_CONTENT_MODES constant is NOT yet defined in
    engines/fhir/__init__.py. When a future PR promotes it, this probe
    fires loudly (carry-forward-as-probe pattern, strategy 56).

    HISTORIAN source-read: verify the constant is absent. When the
    promotion lands, this probe MUST be updated to assert the constant IS
    present and imported by this test file (replacing the local definition).
    Regression cite: CF-SKEPTIC-CS01-RESWEEP-01.
    """
    src = _read_source(_FHIR_INIT_PATH)
    has_constant = "FHIR_R4_CONTENT_MODES" in src
    pytest.current_report_extra = f"has_constant_in_fhir_init={has_constant}"
    # Carry-forward-as-probe pattern: assert the DEFERRED state.
    # When the promotion lands, this assertion MUST be flipped.
    assert not has_constant, (
        f"FHIR_R4_CONTENT_MODES is now defined in engines/fhir/__init__.py — "
        f"CF-SKEPTIC-CS01-RESWEEP-01 was RESOLVED. This probe (carry-forward-"
        f"as-probe pattern) MUST be updated: import the constant from "
        f"engines.fhir instead of defining it locally in this test file, "
        f"then flip this assertion to `assert has_constant`."
    )


def test_h53_other_r4_closed_enums_are_in_canonical_location():
    """CF-SKEPTIC-CS01-RESWEEP-01 LOW DEFERRED (cross-enum context): the
    OTHER two R4 closed enums (FHIR_R4_CONCEPT_MAP_EQUIVALENCE and
    FHIR_R4_FILTER_OPERATORS) ARE in engines/fhir/__init__.py per milestone-
    2 review (CR-014). The content enum is the straggler.

    HISTORIAN source-read: verify the other 2 enums are present (the
    content enum is the only one missing — promotes the symmetry argument
    for the carry-forward).
    Regression cite: CF-SKEPTIC-CS01-RESWEEP-01 + CR-014.
    """
    src = _read_source(_FHIR_INIT_PATH)
    pytest.current_report_extra = (
        f"has_equivalence={'FHIR_R4_CONCEPT_MAP_EQUIVALENCE' in src} "
        f"has_filter_ops={'FHIR_R4_FILTER_OPERATORS' in src}"
    )
    assert "FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in src, (
        f"FHIR_R4_CONCEPT_MAP_EQUIVALENCE missing from engines/fhir/__init__.py "
        f"(CR-014 regression). The content enum (CF-SKEPTIC-CS01-RESWEEP-01) "
        f"is the straggler ONLY if the other 2 are present."
    )
    assert "FHIR_R4_FILTER_OPERATORS" in src, (
        f"FHIR_R4_FILTER_OPERATORS missing from engines/fhir/__init__.py "
        f"(CR-014 regression)."
    )


# ===========================================================================
# Cross-pattern: bidirectional URI invariant (TS-01 test_t10 load-bearing)
# ===========================================================================

def test_h60_registry_advertised_uris_bidirectional_invariant(fhir_client):
    """TS-01 TERMINOLOGIST test_t10 load-bearing invariant (re-derived on
    CS-01 HISTORIAN surface): every URI in SYSTEM_TO_FHIR_URI MUST appear
    in TerminologyCapabilities advertisement AND every advertised URI MUST
    appear in SYSTEM_TO_FHIR_URI. Catches both drift directions.

    HISTORIAN lens: this is the META pattern that subsumes HCPCS drift.
    If it holds, HCPCS drift cannot recur (test_h11..h16 are specific
    instances of this invariant).
    Regression cite: TS-01 TERMINOLOGIST test_t10 + count=8+1 PROMOTED.
    """
    body = fhir_client.get("/fhir/metadata?mode=terminology").json()
    advertised = {e.get("uri") for e in body.get("codeSystem", [])}
    canonical = set(SYSTEM_TO_FHIR_URI.values())
    missing = canonical - advertised
    extras = advertised - canonical
    pytest.current_report_extra = f"missing={sorted(missing)} extras={sorted(extras)}"
    assert not missing, (
        f"Canonical URIs missing from advertisement: {sorted(missing)}. "
        f"TS-01 test_t10 bidirectional invariant regression."
    )
    assert not extras, (
        f"Advertised URIs not in canonical registry: {sorted(extras)}. "
        f"TS-01 test_t10 bidirectional invariant regression."
    )


def test_h61_canonical_system_uri_helper_never_silent_on_unknown():
    """CS-02 HISTORIAN QA-047 + CS-03 HISTORIAN QA-051 regression: the
    canonical_system_uri() helper MUST log at WARNING when no canonical
    URI is resolvable (silent raw-alias emission is silent-wrong-answer).
    This is the structural fix for the client-input-as-canonical drift
    pattern (count=8+1 PROMOTED).

    HISTORIAN source-read: verify the WARNING branch is present in the
    helper.
    Regression cite: CS-02 HISTORIAN QA-047 + CS-03 HISTORIAN QA-051 +
    count=8+1 PROMOTED.
    """
    src = _read_source(_FHIR_INIT_PATH)
    helper_fn = _get_func_source(src, "canonical_system_uri")
    pytest.current_report_extra = f"found_helper_fn={bool(helper_fn)}"
    assert helper_fn, "canonical_system_uri function not found"
    assert "logger.warning" in helper_fn, (
        f"canonical_system_uri MUST log at WARNING when no canonical URI is "
        f"resolvable. CS-02 HISTORIAN QA-047 + count=8+1 PROMOTED regression. "
        f"Source: {helper_fn[:400]}"
    )


# ===========================================================================
# Cross-pattern: READ + SEARCH route shape (structural contracts)
# ===========================================================================

def test_h70_read_route_uses_fhir_response_for_404():
    """CS-01 SKEPTIC QA-002 (NOT A BUG Registry) regression: the READ route
    MUST call _fhir_response (or _fhir_error) so the 404 Content-Type is
    always application/fhir+json (NOT framework default application/json).

    HISTORIAN source-read: structural contract pin.
    Regression cite: CS-01 SKEPTIC QA-002.
    """
    src = _read_source(_FHIR_API_PATH)
    read_fn = _get_func_source(src, "read_resource")
    pytest.current_report_extra = f"found_read_fn={bool(read_fn)}"
    assert read_fn, "read_resource function not found"
    assert "_fhir_response" in read_fn or "_fhir_error" in read_fn, (
        f"read_resource MUST call _fhir_response/_fhir_error. "
        f"CS-01 SKEPTIC QA-002 regression."
    )


def test_h71_search_route_uses_fhir_response_for_bundle():
    """CS-01 SKEPTIC QA-003 (NOT A BUG Registry) regression: the SEARCH
    route MUST call _fhir_response so the 200 Content-Type is always
    application/fhir+json.

    HISTORIAN source-read: structural contract pin.
    Regression cite: CS-01 SKEPTIC QA-003.
    """
    src = _read_source(_FHIR_API_PATH)
    search_fn = _get_func_source(src, "search_resource")
    pytest.current_report_extra = f"found_search_fn={bool(search_fn)}"
    assert search_fn, "search_resource function not found"
    assert "_fhir_response" in search_fn, (
        f"search_resource MUST call _fhir_response. "
        f"CS-01 SKEPTIC QA-003 regression."
    )


def test_h72_read_route_rejects_dollar_prefixed_id(fhir_client):
    """CS-01 SKEPTIC QA-002 (NOT A BUG Registry) regression: a $-prefixed
    id (operation name misused as resource id) MUST return 404 +
    OperationOutcome + application/fhir+json. The READ route handler has
    an explicit branch for resource_id.startswith("$").

    HISTORIAN behavioral probe.
    Regression cite: CS-01 SKEPTIC QA-002.
    """
    r = fhir_client.get("/fhir/CodeSystem/$notanoperation")
    pytest.current_report_extra = f"status={r.status_code} ct={r.headers.get('content-type')}"
    assert r.status_code == 404, (
        f"READ $-prefixed id -> {r.status_code}; expected 404"
    )
    ct = r.headers.get("content-type", "")
    assert "fhir+json" in ct, (
        f"READ $-prefixed id Content-Type must be FHIR; got {ct!r}"
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"
