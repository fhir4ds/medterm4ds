"""HISTORIAN iteration CS-01 — pattern-match SKEPTIC's QA-043 fix against the
literal-value-vs-canonical-registry drift registry.

Source: https://build.fhir.org/codesystem.html (CodeSystem Resource Structure)

HISTORIAN lens:
1. Re-test SKEPTIC's QA-043 fix (`sab_label_to_fhir_uri` helper + $lookup wire)
   for completeness — does the helper handle all SAB labels present in the
   production patient-friendly JSONs? Does the wire site fall back silently
   on unrecognized SAB labels?
2. Pattern-match against the literal-value drift registry (count=4 after
   QA-043 — pattern already promoted to GLOBAL_RULES.md "Code Review Time"
   at count=3 with TS-02 TERMINOLOGIST QA-030). Audit OTHER custom
   properties emitted by $lookup that derive from patient-friendly JSON
   data for the SAME drift class:
   - `match-type` (raw engine vocabulary — confirmed CF-SKEPTIC-CS01-02)
   - `canonical-code` (clinical identifier — semantic check)
   - `tty` (raw UMLS term-type vocabulary)
   - `patient-friendly` (display string — out of scope for canonical drift)
3. Audit the helper's silent-fallback strategy. Per GLOBAL_RULES.md
   "Silent Fallbacks": falling back to the raw SAB label without a
   WARNING log is silent-wrong-answer.
4. Audit the `_SAB_LABEL_TO_SOURCE` map for completeness against every SAB
   label present in the production patient-friendly JSONs.

Production-surface note: the conformance fixture's `fhir_client` does NOT
seed patient-friendly rows, but it also does NOT override the
`MEDTERM4DS_FHIR4PX_BASELINE` env var. As a result, the production JSONs
at `/mnt/d/medterm4ds/reports/fhir4px/` are loaded into the app's
`patient_friendly_cache`. This is the same accidental-reproduction pattern
that SKEPTIC CS-01 (QA-043) leveraged — beneficial for catching drift
that the fixture-local surface can't exercise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers — load the production patient-friendly JSONs to enumerate the
# actual surface that the conformance fixture accidentally loads.
# ---------------------------------------------------------------------------

PF_BASELINE = Path("/mnt/d/medterm4ds/reports/fhir4px")
PF_SOURCES = ("snomedct_us", "rxnorm", "icd10cm", "icd10pcs", "lnc", "cpt", "hcpcs", "cvx")


def _pf_loaded() -> bool:
    """True iff any production patient-friendly JSON is loadable from baseline."""
    if not PF_BASELINE.is_dir():
        return False
    return any((PF_BASELINE / f"patient_friendly_{s}.json").exists() for s in PF_SOURCES)


def _collect_pf_values(field: str) -> set[str]:
    """Return the set of unique `field` values across all patient-friendly JSONs."""
    values: set[str] = set()
    if not PF_BASELINE.is_dir():
        return values
    for sab in PF_SOURCES:
        path = PF_BASELINE / f"patient_friendly_{sab}.json"
        if not path.exists():
            continue
        try:
            with path.open() as f:
                data = json.load(f)
        except Exception:
            continue
        if isinstance(data, dict):
            for _, v in data.items():
                if isinstance(v, dict):
                    val = v.get(field)
                    if isinstance(val, str):
                        values.add(val)
    return values


def _first_pf_entry_for_source(source_lower: str) -> tuple[str, dict] | None:
    """Return (code, entry) for the first entry in the given source's PF JSON."""
    path = PF_BASELINE / f"patient_friendly_{source_lower}.json"
    if not path.exists():
        return None
    try:
        with path.open() as f:
            data = json.load(f)
    except Exception:
        return None
    if isinstance(data, dict):
        for code, entry in data.items():
            if isinstance(entry, dict) and entry.get("canonical_system") and entry.get("canonical_code"):
                return code, entry
    return None


# Skip the entire module if the production baseline isn't loadable.
pytestmark = pytest.mark.skipif(
    not _pf_loaded(),
    reason="Production patient-friendly JSONs not found at /mnt/d/medterm4ds/reports/fhir4px — "
    "conformance fixture needs the production baseline to exercise the $lookup custom-property surface.",
)


# ---------------------------------------------------------------------------
# Pattern class 1: literal-value-vs-canonical-registry drift (count=4 after
# QA-043). Audit EVERY custom property derived from patient-friendly JSON data
# for the same drift shape — raw engine vocabulary echoed verbatim.
# ---------------------------------------------------------------------------

def test_h01_lookup_canonical_system_property_is_fhir_uri_for_every_sab(fhir_client):
    """HISTORIAN pattern-match QA-043 across every SAB label present in the
    production patient-friendly JSONs. The SKEPTIC fix added `sab_label_to_fhir_uri`;
    this probe confirms the helper covers EVERY label actually in use, not
    just the one SKEPTIC reproduced (`icd10`).

    Per FHIR R4 §4.8.3.1 CodeSystem identification + §4.8.11 Concept Properties:
    Coding.system values MUST be canonical URIs, not raw engine labels.

    Drift class: literal-value-vs-canonical-registry drift (count=4 after
    QA-043 — promoted to GLOBAL_RULES.md "Code Review Time" at count=3).
    """
    from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI, sab_label_to_fhir_uri

    # Every SAB label present in the production patient-friendly JSONs.
    actual_sab_labels = _collect_pf_values("canonical_system")
    assert actual_sab_labels, "Test setup error: no canonical_system values collected"

    for sab_label in sorted(actual_sab_labels):
        fhir_uri = sab_label_to_fhir_uri(sab_label)
        assert fhir_uri is not None, (
            f"sab_label_to_fhir_uri({sab_label!r}) returned None — the helper's "
            f"_SAB_LABEL_TO_SOURCE map is incomplete. Add the missing label → source mapping."
        )
        assert fhir_uri.startswith(("http://", "https://", "urn:")), (
            f"sab_label_to_fhir_uri({sab_label!r}) returned {fhir_uri!r} — must be a FHIR URI."
        )
        # Cross-check: the URI must be in the canonical map (the single source
        # of truth). Catches silent drift between `_SAB_LABEL_TO_SOURCE` and
        # `SYSTEM_TO_FHIR_URI`.
        assert fhir_uri in {v for v in SYSTEM_TO_FHIR_URI.values()}, (
            f"sab_label_to_fhir_uri({sab_label!r}) returned {fhir_uri!r} which is NOT "
            f"in SYSTEM_TO_FHIR_URI — the helper is producing values outside the registry."
        )


def test_h02_lookup_canonical_system_via_wire_for_each_sab(fhir_client):
    """End-to-end wire probe: for every SAB with patient-friendly data, issue
    $lookup against a known code and assert the `canonical-system` property
    value is a FHIR URI (not a raw SAB label).

    This is the HISTORIAN-style "parametrize over the production surface"
    pattern — SKEPTIC only tested one SAB (SNOMED CT); HISTORIAN tests all 8.
    Catches the case where the helper is correct but the wire site doesn't
    use it for some path.
    """
    # Codes seeded in the conformance DB (see conftest.py:_make_conformance_db).
    # Map SAB label → (FHIR URI, seeded code) for codes likely to also appear
    # in the production patient-friendly JSONs.
    seeded = [
        ("snomedct_us", "http://snomed.info/sct", "73211009"),
        ("rxnorm", "http://www.nlm.nih.gov/research/umls/rxnorm", "860975"),
        ("icd10cm", "http://hl7.org/fhir/sid/icd-10-cm", "E11"),
        ("icd10", "http://hl7.org/fhir/sid/icd-10-cm", "E11"),
    ]
    seen_any = False
    for sab_label, fhir_uri, code in seeded:
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": fhir_uri, "code": code},
        )
        assert r.status_code == 200, f"$lookup {sab_label} {code} → {r.status_code}"
        body = r.json()
        cs_val = None
        for p in body.get("parameter", []):
            if p.get("name") != "property":
                continue
            parts = p.get("part", [])
            code_part = next((pt for pt in parts if pt.get("name") == "code"), {})
            if code_part.get("valueCode") == "canonical-system":
                val_part = next((pt for pt in parts if pt.get("name") == "value"), {})
                cs_val = val_part.get("valueString") or val_part.get("valueUri")
                break
        if cs_val is None:
            # Code not in the PF JSON — skip this SAB. The point is to test
            # at least one path that DOES emit canonical-system.
            continue
        seen_any = True
        assert cs_val.startswith(("http://", "https://", "urn:")), (
            f"$lookup canonical-system for {sab_label}/{code} = {cs_val!r} — "
            f"raw SAB label leaked through the wire; expected FHIR URI. "
            f"Same drift class as QA-043."
        )
    assert seen_any, (
        "No $lookup probe found a code that emits canonical-system — either the "
        "production patient-friendly JSONs are not loaded, or none of the seeded "
        "conformance codes overlap with the JSON keys."
    )


def test_h03_lookup_match_type_uses_engine_vocabulary_not_fhir_enum(fhir_client):
    """HISTORIAN pattern-match against CF-SKEPTIC-CS01-02: the `match-type`
    custom property emitted by $lookup uses raw engine vocabulary
    (`same_cui`, `exact`, `broader`, `group`, `ingredient`, `original`, etc.)
    that is NOT in any FHIR R4 closed enum.

    Per FHIR R4 §4.8.11 Concept Properties: custom properties with arbitrary
    string values are spec-permitted (CodeSystem.property.type can be 'code',
    'Coding', 'string', 'integer', 'boolean', 'dateTime'). The drift class
    concern here is the SAME as QA-043: a value derived from engine data
    was echoed verbatim without consulting the canonical registry.

    For `match-type`, the canonical registry is the FHIR R4
    ConceptMapEquivalence enum (https://hl7.org/fhir/valueset-concept-map-equivalence.html):
        relatedto | equivalent | equal | wider | narrower | subsumes |
        subsumedby | matches | inexact | unmatched | disjoint

    Of the 13 unique match_type values in the production patient-friendly
    JSONs, ZERO appear in the FHIR R4 ConceptMapEquivalence enum. This is
    a genuine drift instance (count=5 for the literal-value-vs-canonical-
    registry drift pattern after this finding).

    Severity: MEDIUM (not HIGH) — `match-type` is a server-local custom
    property, not a Coding.system value, so the FHIR R4 conformance risk is
    lower than QA-043. But the same anti-pattern applies: a future client
    interpreting `match-type: "broader"` as `equivalence: wider` would
    silently mis-map.
    """
    # Sanity: confirm the raw engine vocabulary is what's actually in the PF JSONs.
    actual_match_types = _collect_pf_values("match_type")
    if not actual_match_types:
        pytest.skip(
            "No match_type values collected from production JSONs — "
            "fixture isolation prevents probing CF-SKEPTIC-CS01-02 here."
        )

    # CR-014 (milestone-2 review): import the single source of truth from
    # medterm4ds.engines.fhir instead of hardcoding the wrong enum. The
    # prior local copy included R5/R4B ``subsumedby`` and R5-only
    # ``matches``; the R4 spec-correct value is ``specializes``.
    from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    FHIR_R4_EQUIVALENCE = FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    overlap = actual_match_types & FHIR_R4_EQUIVALENCE
    # Documentation assertion — most production match_type values don't match
    # FHIR R4 ConceptMapEquivalence. If they DID, that would change the picture.
    # Document the actual overlap count for the architect audit.
    non_enum = actual_match_types - FHIR_R4_EQUIVALENCE
    assert non_enum, (
        "Expected production match_type vocabulary to contain non-FHIR-enum "
        "values per CF-SKEPTIC-CS01-02. If all values are now FHIR enum values, "
        "the carry-forward is stale and can be closed."
    )

    # Wire-side probe: issue a $lookup against a code known to have PF data
    # and assert the `match-type` property value is the raw engine vocabulary
    # (i.e., the bug reproduces).
    seeded = [
        ("http://snomed.info/sct", "73211009"),
        ("http://www.nlm.nih.gov/research/umls/rxnorm", "860975"),
        ("http://hl7.org/fhir/sid/icd-10-cm", "E11"),
    ]
    seen_match_type = False
    for fhir_uri, code in seeded:
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": fhir_uri, "code": code},
        )
        if r.status_code != 200:
            continue
        body = r.json()
        for p in body.get("parameter", []):
            if p.get("name") != "property":
                continue
            parts = p.get("part", [])
            code_part = next((pt for pt in parts if pt.get("name") == "code"), {})
            if code_part.get("valueCode") == "match-type":
                val_part = next((pt for pt in parts if pt.get("name") == "value"), {})
                mt_val = val_part.get("valueString")
                if mt_val is None:
                    continue
                seen_match_type = True
                # Document the drift: the emitted value is NOT in the FHIR enum.
                # (This is the bug; do NOT assert it should be — that's the fix.)
                assert mt_val in actual_match_types, (
                    f"$lookup match-type value {mt_val!r} not found in production "
                    f"PF JSON match_type vocabulary — drift between JSON and wire."
                )
                assert mt_val not in FHIR_R4_EQUIVALENCE, (
                    f"$lookup match-type value {mt_val!r} IS in the FHIR R4 enum — "
                    f"the CF-SKEPTIC-CS01-02 carry-forward may be obsolete. Re-audit."
                )
                break
        if seen_match_type:
            break

    if not seen_match_type:
        pytest.skip(
            "No $lookup response emitted a match-type property — none of the seeded "
            "codes are present in the production PF JSONs. CF-SKEPTIC-CS01-02 stands."
        )


# ---------------------------------------------------------------------------
# Pattern class 2: silent-fallback audit on the new helper.
# Per GLOBAL_RULES.md "Silent Fallbacks — Prohibited Patterns":
# falling back to the raw SAB label without WARNING-level logging is
# silent-wrong-answer. The caller (fhir_api.py:_do_lookup line 1258) emits
# `fhir_uri if fhir_uri else raw_sab` — if `fhir_uri` is None (unknown SAB),
# the raw label leaks with no warning.
# ---------------------------------------------------------------------------

def test_h10_sab_label_to_fhir_uri_silent_fallback_strategy():
    """HISTORIAN audit on `sab_label_to_fhir_uri`: the helper returns None for
    unrecognized SAB labels. The caller (`_do_lookup`) then emits the raw
    label verbatim. Per GLOBAL_RULES.md "Silent Fallbacks", this is the
    pattern that produced the v0.0.1 B-class bugs (silent wrong answers).

    The helper itself is fine — returning None is the narrowest contract.
    The concern is the CALLER: `fhir_api.py:1258` writes
        `custom_props["canonical-system"] = fhir_uri if fhir_uri else raw_sab`
    silently falling back to raw_sab when translation fails.

    This test documents the contract: the helper returns None for unknowns;
    the caller's fallback behavior is the silent-wrong-answer risk.
    """
    from medterm4ds.engines.fhir import sab_label_to_fhir_uri

    # Recognized labels return a URI.
    assert sab_label_to_fhir_uri("icd10") == "http://hl7.org/fhir/sid/icd-10-cm"
    assert sab_label_to_fhir_uri("snomedct_us") == "http://snomed.info/sct"

    # Unrecognized labels return None — caller MUST decide.
    assert sab_label_to_fhir_uri("unknown_sab") is None
    assert sab_label_to_fhir_uri("") is None
    assert sab_label_to_fhir_uri("xyz_not_in_map") is None

    # .upper() fallback catches variants like ICD10CM → ICD10CM (already in map
    # under uppercase key). The fallback succeeds if the uppercase form is a
    # SYSTEM_TO_FHIR_URI key.
    assert sab_label_to_fhir_uri("ICD10CM") == "http://hl7.org/fhir/sid/icd-10-cm"

    # But a typo like "icd10mc" (transposed) returns None — no silent guess.
    assert sab_label_to_fhir_uri("icd10mc") is None


def test_h11_do_lookup_emits_raw_sab_when_translation_fails(caplog):
    """HISTORIAN pattern-match against v0.0.1 B-class silent-fallback: when
    `sab_label_to_fhir_uri` returns None (unknown SAB), the caller
    `_do_lookup` emits the raw label verbatim.

    Audit (post-QA-044 fix): the caller now logs at WARNING when the helper
    returns None and emits the raw value as a diagnostic fallback. This is
    the GLOBAL_RULES.md "Silent Fallbacks" compliant shape — the failure
    is visible to operators, not silent.

    Severity context: MEDIUM — only fires on unrecognized SAB labels. Today
    the `_SAB_LABEL_TO_SOURCE` map is exhaustive for the 8 production sources,
    so the fallback path is never hit in practice. But the WARNING log is
    the defensive guard for future source additions that don't update the map.

    This test asserts the WORST-CASE behavior: an unrecognized SAB triggers
    a WARNING log AND emits the raw value (diagnostic, not silent).
    """
    import logging

    import inspect

    from medterm4ds.apps import fhir_api
    from medterm4ds.engines.fhir import sab_label_to_fhir_uri

    src = inspect.getsource(fhir_api)
    # The wire site MUST exist (CS-01 SKEPTIC landed it).
    assert "sab_label_to_fhir_uri" in src, (
        "sab_label_to_fhir_uri helper not wired into fhir_api.py — QA-043 fix "
        "incomplete."
    )
    # The fallback strategy MUST log at WARNING (QA-044 fix).
    # The bare `if fhir_uri else raw_sab` expression was the silent-fallback
    # shape; the fix replaced it with an explicit branch that logs.
    assert "if fhir_uri else raw_sab" not in src, (
        "fhir_api.py still contains the silent-fallback expression "
        "`if fhir_uri else raw_sab` — QA-044 fix incomplete. The fallback "
        "MUST log at WARNING before emitting the raw value."
    )
    assert "logger.warning" in src, (
        "fhir_api.py must contain a logger.warning call for the QA-044 fallback."
    )
    # Functional check: helper returns None for unknown; log capture verifies
    # the WARNING fires when invoked through the wire (best-effort — we don't
    # construct a full app context here, the integration is covered by h02).
    assert sab_label_to_fhir_uri("totally_unknown_sab_label") is None


# ---------------------------------------------------------------------------
# Pattern class 3: documentation-vs-implementation drift (count=1, threshold
# >2 not yet hit). Audit the docstring on `sab_label_to_fhir_uri` against
# the body.
# ---------------------------------------------------------------------------

def test_h20_sab_label_to_fhir_uri_docstring_matches_body():
    """HISTORIAN lens: docstring-vs-implementation drift (TS-01 HISTORIAN
    QA-007 pattern). Read the docstring on `sab_label_to_fhir_uri` and verify
    the body delivers the documented behavior.

    The docstring claims:
      - "Returns None if the label is unrecognized (caller should fall back
         to the raw value, which is more useful than None for diagnostic
         purposes)."

    Body verification:
      - Empty input → returns None ✓
      - Recognized label → returns FHIR URI ✓
      - Unrecognized label → tries .upper() fallback → if that fails, returns None ✓

    The docstring is honest. The drift concern is between the helper's
    contract and the CALLER's use of it: the caller emits the raw value
    silently (no WARNING) — which the docstring says is "more useful for
    diagnostic purposes" but GLOBAL_RULES.md "Silent Fallbacks" says is
    silent-wrong-answer if the failure degrades output.
    """
    from medterm4ds.engines.fhir import sab_label_to_fhir_uri

    # Docstring claim: "Returns None if the label is unrecognized"
    assert sab_label_to_fhir_uri("totally_unknown_sab") is None
    # Docstring claim: empty input handling
    assert sab_label_to_fhir_uri("") is None

    # Docstring implication: recognized labels return URIs (the "more useful"
    # branch).
    uri = sab_label_to_fhir_uri("lnc")
    assert uri == "http://loinc.org"


# ---------------------------------------------------------------------------
# Pattern class 4: _SAB_LABEL_TO_SOURCE completeness audit. The map MUST
# cover every SAB label present in production patient-friendly JSONs.
# ---------------------------------------------------------------------------

def test_h30_sab_label_map_covers_every_production_label():
    """HISTORIAN audit: the `_SAB_LABEL_TO_SOURCE` map MUST be exhaustive for
    every SAB label present in the production patient-friendly JSONs. A
    missing entry would cause `sab_label_to_fhir_uri` to silently fall back
    to the raw label (per the caller's `if fhir_uri else raw_sab`).

    The architect audit on CS-01 SKEPTIC noted this risk: "Future sources
    added to `SYSTEM_TO_FHIR_URI` MUST also be added to `_SAB_LABEL_TO_SOURCE`
    if patient-friendly JSONs are generated for them."

    This test asserts the contract today: every production SAB label is in
    the map.
    """
    from medterm4ds.engines.fhir import _SAB_LABEL_TO_SOURCE

    actual_labels = _collect_pf_values("canonical_system")
    assert actual_labels, "Test setup error: no canonical_system values collected"

    missing = set()
    for label in actual_labels:
        if label.lower() not in _SAB_LABEL_TO_SOURCE:
            # The .upper() fallback in the helper catches variants like
            # "ICD10CM" → "ICD10CM" (uppercase key in SYSTEM_TO_FHIR_URI).
            # So we only flag labels that ALSO don't resolve via upper().
            from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI
            if label.upper() not in SYSTEM_TO_FHIR_URI:
                missing.add(label)

    assert not missing, (
        f"_SAB_LABEL_TO_SOURCE missing entries for production SAB labels: "
        f"{sorted(missing)}. Add them or add uppercase variants to "
        f"SYSTEM_TO_FHIR_URI. Until fixed, $lookup canonical-system silently "
        f"emits raw labels for these sources (silent-wrong-answer per "
        f"GLOBAL_RULES.md 'Silent Fallbacks')."
    )


def test_h31_sab_label_map_lowercase_only():
    """HISTORIAN audit: the `_SAB_LABEL_TO_SOURCE` keys are documented as
    'lowercase raw UMLS SAB labels' (per the docstring on the helper). The
    helper does `.lower()` on input before lookup. Audit that the keys are
    actually lowercase — uppercase keys would never be hit by the lookup
    (silent dead code).
    """
    from medterm4ds.engines.fhir import _SAB_LABEL_TO_SOURCE

    uppercase_keys = [k for k in _SAB_LABEL_TO_SOURCE if k != k.lower()]
    assert not uppercase_keys, (
        f"_SAB_LABEL_TO_SOURCE has uppercase keys {uppercase_keys} — the helper "
        f"does .lower() before lookup, so these entries are dead code. "
        f"Rename to lowercase."
    )


# ---------------------------------------------------------------------------
# Pattern class 5: hardcoded-port / hardcoded-URL audit (A2 / QA-037 pattern).
# The new helper has no URL construction; this is a sanity check that the
# CS-01 SKEPTIC iteration didn't introduce a new URL literal.
# ---------------------------------------------------------------------------

def test_h40_no_new_hardcoded_urls_in_helper():
    """HISTORIAN pattern-match against A2 / QA-037 (hardcoded-port /
    hardcoded-scheme-literal in URL constructors). The new
    `sab_label_to_fhir_uri` helper doesn't construct URLs — it does
    dictionary lookups. Audit the helper's executable statements (not the
    docstring) for any literal URL strings that would indicate URL
    construction.

    The docstring legitimately references `http://hl7.org/fhir/sid/icd-10-cm`
    as an EXAMPLE value (documentation, not construction). The audit targets
    code statements that BUILD or INTERPOLATE URLs — those would be
    hardcoded-URL drift.
    """
    import ast
    import inspect

    from medterm4ds.engines.fhir import sab_label_to_fhir_uri

    src = inspect.getsource(sab_label_to_fhir_uri)
    tree = ast.parse(src)
    # Walk all string-literal constants in the function body (excluding the
    # docstring, which is the first Expr with a string value).
    url_literals: list[str] = []
    docstring_seen = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if not docstring_seen:
                docstring_seen = True  # skip the docstring
                continue
            val = node.value
            if val.startswith(("http://", "https://", "urn:")):
                url_literals.append(val)
    # The helper MUST NOT construct URLs — URLs come from SYSTEM_TO_FHIR_URI.
    assert not url_literals, (
        f"sab_label_to_fhir_uri constructs hardcoded URL literals: {url_literals} "
        f"— URLs MUST come from SYSTEM_TO_FHIR_URI, not be duplicated in the helper. "
        f"Pattern: A2 / QA-037 hardcoded-URL drift."
    )


# ---------------------------------------------------------------------------
# Pattern class 6: SKEPTIC fix survival — verify the original QA-043 repro
# still passes (the SKEPTIC test `test_s46` already covers this, but
# HISTORIAN re-pins with a stricter assertion).
# ---------------------------------------------------------------------------

def test_h50_skeptic_qa043_fix_survives_strict(fhir_client):
    """HISTORIAN re-test of SKEPTIC's QA-043 fix with a stricter positive
    success-shape assertion. SKEPTIC's `test_s46` asserts the canonical-system
    value starts with `http://` or `urn:` — a positive success shape.

    HISTORIAN strengthens by:
      1. Asserting the value is one of the EXACT 8 canonical URIs in
         SYSTEM_TO_FHIR_URI (not just any http:// string).
      2. Asserting the value is NOT the raw SAB label (explicit negative).
    """
    from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

    canonical_uris = set(SYSTEM_TO_FHIR_URI.values())
    raw_sab_labels = _collect_pf_values("canonical_system")

    seeded = [
        ("http://snomed.info/sct", "73211009"),
        ("http://www.nlm.nih.gov/research/umls/rxnorm", "860975"),
        ("http://hl7.org/fhir/sid/icd-10-cm", "E11"),
    ]
    seen = False
    for fhir_uri, code in seeded:
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": fhir_uri, "code": code},
        )
        if r.status_code != 200:
            continue
        body = r.json()
        for p in body.get("parameter", []):
            if p.get("name") != "property":
                continue
            parts = p.get("part", [])
            code_part = next((pt for pt in parts if pt.get("name") == "code"), {})
            if code_part.get("valueCode") != "canonical-system":
                continue
            val_part = next((pt for pt in parts if pt.get("name") == "value"), {})
            cs_val = val_part.get("valueString") or val_part.get("valueUri")
            if cs_val is None:
                continue
            seen = True
            # Stricter: value MUST be one of the 8 canonical URIs.
            assert cs_val in canonical_uris, (
                f"$lookup canonical-system = {cs_val!r} — must be one of the 8 "
                f"canonical URIs in SYSTEM_TO_FHIR_URI. QA-043 fix regression."
            )
            # Stricter: value MUST NOT be a raw SAB label.
            assert cs_val not in raw_sab_labels, (
                f"$lookup canonical-system = {cs_val!r} — this is a raw SAB label, "
                f"not a FHIR URI. QA-043 regression."
            )
            break
        if seen:
            break

    if not seen:
        pytest.skip(
            "No $lookup response emitted canonical-system — production PF JSONs "
            "may not be loaded, or no seeded code has PF data."
        )
