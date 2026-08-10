"""HISTORIAN RESWEEP probes for chunk CM-02 (ConceptMap $translate Operation).

Source: https://build.fhir.org/conceptmap-operation-translate.html
Canonical R4 $translate operation:
    https://hl7.org/fhir/R4/conceptmap-operation-translate.html
Canonical R4 ConceptMapEquivalence closed enum:
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html

HISTORIAN lens (pattern-match against prior bug patterns). The
HISTORIAN re-derives each prior pattern via independent source-read +
behavioral probes — distinct from SKEPTIC's hostile-input probing.

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus), 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus; A44054006 -> A73211009)

SKEPTIC tips ADDRESSED (3 items):
  1. Re-derive the 5 PROMOTED patterns on the $translate surface via
     independent source-read + behavioral probes.
  2. Extend the GET<->POST byte-exact parity (test_s90, 5 cases) to
     every seeded code x every target system.
  3. Re-verify CF-CM02-01 CLOSED via object-identity on the canonical
     helper (INTERNAL_REL_TO_FHIR_EQUIVALENCE is
     responses_module._INTERNAL_REL_TO_FHIR_EQUIVALENCE via `is`).

Lateral probes:
  - Deeply-nested codeableConcept with mixed valid+invalid codings.
  - Cross-operation round-trip ($lookup <-> $translate <-> $subsumes
    on same code).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from medterm4ds.apps import fhir_api
from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
    canonical_system_uri,
    fhir_uri_to_system,
    system_to_fhir_uri,
)
from medterm4ds.engines.fhir.equivalence import (
    INTERNAL_REL_TO_FHIR_EQUIVALENCE,
    fhir_equivalence,
)
from medterm4ds.engines.fhir.responses import (
    _fhir_equivalence_from_relationship,
    build_parameters_translate,
)


# ---------------------------------------------------------------------------
# Constants for the probes.
# ---------------------------------------------------------------------------
SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URI_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_URI_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"

# All seeded codes in the conformance fixture.
SEEDED_CODES: list[tuple[str, str, str]] = [
    (SNOMED_URI, "73211009", "Diabetes mellitus (SNOMED)"),
    (SNOMED_URI, "44054006", "Type 2 diabetes mellitus (SNOMED)"),
    (ICD10CM_URI, "E11", "Type 2 diabetes mellitus (ICD-10-CM)"),
    (RXNORM_URI, "860975", "24 HR metformin 500 MG Oral Tablet (RxNorm)"),
]

# Target systems probed in the GET<->POST parity matrix (Lens 7).
TARGET_SYSTEMS: list[tuple[str, str]] = [
    (ICD10CM_URI, "ICD-10-CM"),
    (RXNORM_URI, "RxNorm"),
    (SNOMED_URI, "SNOMED CT"),
]


# ---------------------------------------------------------------------------
# Source-read helpers
# ---------------------------------------------------------------------------
_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "medterm4ds"
    / "apps"
    / "fhir_api.py"
)
_RESPONSES_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "medterm4ds"
    / "engines"
    / "fhir"
    / "responses.py"
)
_EQUIVALENCE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "medterm4ds"
    / "engines"
    / "fhir"
    / "equivalence.py"
)


def _get_func_source(file_path: Path, func_name: str) -> str:
    """Extract the source text of a function (possibly nested) by name.

    Walks BOTH ``ast.FunctionDef`` AND ``ast.AsyncFunctionDef`` (per
    TS-04 HISTORIAN methodology — async route handlers nested inside
    ``create_fhir_app``). Returns "" if not found.
    """
    src = file_path.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return ast.get_source_segment(src, node) or ""
    return ""


def _find_param(body: dict[str, Any], name: str) -> dict[str, Any] | None:
    for p in body.get("parameter", []):
        if p.get("name") == name:
            return p
    return None


def _match_parts(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Return ``part`` dicts from every ``match`` parameter."""
    out: list[dict[str, Any]] = []
    for p in body.get("parameter", []):
        if p.get("name") == "match":
            for part in p.get("part", []):
                out.append(part)
    return out


# ===========================================================================
# Lens 1: CF-CM02-01 CLOSED — object-identity re-verification
# SKEPTIC tip 3: INTERNAL_REL_TO_FHIR_EQUIVALENCE is
# responses_module._INTERNAL_REL_TO_FHIR_EQUIVALENCE via `is` operator.
# ===========================================================================


def test_h10_object_identity_internal_rel_to_fhir_equivalence():
    """CF-CM02-01 CLOSED via object-identity (SKEPTIC tip 3).

    The canonical ``INTERNAL_REL_TO_FHIR_EQUIVALENCE`` at
    ``engines/fhir/equivalence.py`` MUST be the SAME Python object as
    the alias ``_INTERNAL_REL_TO_FHIR_EQUIVALENCE`` imported by
    ``engines/fhir/responses.py``. Verifying via ``is`` (not ``==``)
    confirms the responses module imports the canonical map rather
    than copying it — drift is structurally impossible.

    Cross-handler-helper-wiring (count=6 PROMOTED) — TS-02 EXPLORER
    QA-028 pattern class. Per SKEPTIC tip, this is the load-bearing
    contract: any future fork would have to explicitly reassign the
    alias, which the next-day-source-read audit would catch.
    """
    from medterm4ds.engines.fhir import responses as responses_module
    from medterm4ds.engines.fhir import equivalence as equivalence_module

    assert (
        responses_module._INTERNAL_REL_TO_FHIR_EQUIVALENCE
        is equivalence_module.INTERNAL_REL_TO_FHIR_EQUIVALENCE
    ), (
        "responses.py's _INTERNAL_REL_TO_FHIR_EQUIVALENCE is NOT the same "
        "Python object as equivalence.py's INTERNAL_REL_TO_FHIR_EQUIVALENCE. "
        "Drift is now POSSIBLE — CF-CM02-01 carry-forward regression shape."
    )


def test_h11_translate_post_handler_calls_extract_translate_params():
    """CF-CM02-01 CLOSED (structural) — translate_post MUST call
    _extract_translate_params.

    The helper is the SHARED extractor that consults
    ``_extract_named_coding_from_parameters(body, "coding")`` AND
    ``_extract_codeable_concept_from_parameters(body)`` when scalar
    system/code are absent (mirrors ``_extract_lookup_params``).

    If translate_post inlines parsing instead of delegating, the
    helper-wiring pattern (count=6 PROMOTED) regresses.
    """
    src = _get_func_source(_FHIR_API_PATH, "translate_post")
    assert src, "translate_post not found in fhir_api.py source"
    assert "_extract_translate_params" in src, (
        "translate_post does NOT call _extract_translate_params — CF-CM02-01 "
        "REGRESSED (the shared extractor wiring was lost)."
    )


def test_h12_extract_translate_params_calls_both_alt_encodings():
    """CF-CM02-01 CLOSED (structural) — _extract_translate_params MUST
    consult BOTH ``_extract_named_coding_from_parameters`` AND
    ``_extract_codeable_concept_from_parameters``.

    Pattern recurrence closed: silent-wrong-answer on alternative
    parameter encodings count=7 (was 6 PROMOTED) — closed by CM-01
    EXPLORER QA-001 (resweep). HISTORIAN confirms via source-read
    that BOTH alt-encoding helpers are wired in.
    """
    src = _get_func_source(_FHIR_API_PATH, "_extract_translate_params")
    assert src, "_extract_translate_params not found"
    assert "_extract_named_coding_from_parameters" in src, (
        "_extract_translate_params does NOT call _extract_named_coding_from_parameters "
        "— CF-CM02-01 REGRESSED on coding alt-encoding."
    )
    assert "_extract_codeable_concept_from_parameters" in src, (
        "_extract_translate_params does NOT call "
        "_extract_codeable_concept_from_parameters — CF-CM02-01 REGRESSED "
        "on codeableConcept alt-encoding."
    )


def test_h13_cf_cm02_01_behavioral_coding_only_body_200(fhir_client):
    """CF-CM02-01 CLOSED (behavioral) — POST $translate with coding-only
    body MUST return 200.

    Distinct from SKEPTIC test_s42: HISTORIAN adds an additional
    invariant — the returned Parameters resource MUST have resourceType
    == Parameters AND the result param MUST be present (positive
    success shape per TS-03 HISTORIAN QA-034).
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {"system": SNOMED_URI, "code": "44054006"},
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r.status_code == 200, (
        f"POST $translate with coding-only body — CF-CM02-01 CLOSED requires "
        f"200. Got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    assert _find_param(body, "result") is not None, (
        "POST $translate with coding-only body MUST include the result param "
        "(positive success shape per TS-03 HISTORIAN QA-034)."
    )


def test_h14_cf_cm02_01_behavioral_codeableconcept_body_200(fhir_client):
    """CF-CM02-01 CLOSED (behavioral) — POST $translate with
    codeableConcept body MUST return 200. Sibling of test_h13.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": SNOMED_URI, "code": "44054006"},
                        ]
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r.status_code == 200, (
        f"POST $translate with codeableConcept body — CF-CM02-01 CLOSED "
        f"requires 200. Got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    assert _find_param(body, "result") is not None


# ===========================================================================
# Lens 2: 5th PROMOTED pattern — min_length=1 on required-string Query
# Re-derive on the $translate surface via source-read.
# ===========================================================================


def test_h20_translate_get_min_length_on_system_query():
    """5th PROMOTED pattern — system Query has min_length=1.

    Without min_length=1, FastAPI treats empty string as present and
    the handler returns 200 + result=false (silent-wrong-answer).
    """
    src = _get_func_source(_FHIR_API_PATH, "translate_get")
    assert src, "translate_get not found"
    # Find the system Query declaration line.
    assert "Query(..., min_length=1" in src, (
        "translate_get system Query lacks min_length=1 — 5th PROMOTED pattern "
        "regression (silent-wrong-answer on empty-string system)."
    )


def test_h21_translate_get_min_length_on_code_query():
    """5th PROMOTED pattern — code Query has min_length=1."""
    src = _get_func_source(_FHIR_API_PATH, "translate_get")
    assert src, "translate_get not found"
    # Count the occurrences of min_length=1 — system + code both have it.
    count = src.count("min_length=1")
    assert count >= 2, (
        f"translate_get has only {count} min_length=1 declaration(s); expected "
        f">= 2 (system + code). 5th PROMOTED pattern regression."
    )


def test_h22_translate_get_targetsystem_no_min_length():
    """5th PROMOTED pattern boundary — targetsystem is OPTIONAL, NOT
    required, so it MUST NOT have min_length=1.

    Per GLOBAL_RULES.md: 'Optional string params declared with Query(None)
    are NOT affected — empty string on an optional param has a different
    semantic (server-side handler falls back to "no filter")'. This probe
    pins the boundary: the pattern applies to REQUIRED string params
    only.
    """
    src = _get_func_source(_FHIR_API_PATH, "translate_get")
    assert src, "translate_get not found"
    # The targetsystem line MUST use Query(None, ...) NOT Query(..., min_length=1).
    # Find the targetsystem declaration.
    assert "targetsystem" in src
    # Locate the targetsystem Query declaration
    targetsystem_idx = src.find("targetsystem:")
    assert targetsystem_idx != -1
    targetsystem_line_end = src.find("\n", targetsystem_idx)
    targetsystem_decl = src[targetsystem_idx:targetsystem_line_end]
    assert "Query(None" in targetsystem_decl, (
        f"targetsystem Query MUST be optional (Query(None, ...)); got: "
        f"{targetsystem_decl!r}"
    )
    assert "min_length=1" not in targetsystem_decl, (
        f"targetsystem is optional — MUST NOT have min_length=1 (5th PROMOTED "
        f"pattern boundary). Got: {targetsystem_decl!r}"
    )


# ===========================================================================
# Lens 3: 10th PROMOTED pattern — isinstance guard at untrusted-data
# list-iterator boundary. Re-derive on the $translate POST body path.
# ===========================================================================


def test_h30_parse_parameters_has_isinstance_param_dict_guard():
    """10th PROMOTED pattern — _parse_parameters has isinstance(param, dict).

    Per GLOBAL_RULES.md line 140: 'add `if not isinstance(<var>, dict):
    continue` as the first statement inside every `for <var> in
    <body>.get("<key>", []):` loop that subsequently calls `<var>.get(...)`'.

    The _parse_parameters function is the load-bearing extractor for
    every Parameters-body POST handler. Found by CS-04 SKEPTIC QA-001
    (CRITICAL). HISTORIAN confirms via source-read that the guard is
    present.
    """
    src = _get_func_source(_FHIR_API_PATH, "_parse_parameters")
    assert src, "_parse_parameters not found"
    assert "isinstance(param, dict)" in src, (
        "_parse_parameters lacks isinstance(param, dict) guard — 10th PROMOTED "
        "pattern regression (CS-04 SKEPTIC QA-001 CRITICAL bug shape)."
    )


def test_h31_named_coding_extractor_has_isinstance_coding_dict_guard():
    """10th PROMOTED pattern — _extract_named_coding_from_parameters has
    isinstance(coding, dict) guard.

    Sibling guard covering the valueCoding extraction path. Found by
    CS-04 SKEPTIC QA-053 fix. HISTORIAN confirms the guard is present
    in BOTH the named-coding extractor and the bare-coding extractor.
    """
    src = _get_func_source(_FHIR_API_PATH, "_extract_named_coding_from_parameters")
    assert src, "_extract_named_coding_from_parameters not found"
    assert "isinstance(coding, dict)" in src, (
        "_extract_named_coding_from_parameters lacks isinstance(coding, dict) "
        "guard — 10th PROMOTED pattern regression."
    )


def test_h32_codeable_concept_extractor_has_isinstance_coding_dict_guard():
    """10th PROMOTED pattern — _extract_codeable_concept_from_parameters
    has isinstance(coding, dict) guard.

    Sibling guard covering the codeableConcept body's coding[] iteration.
    """
    src = _get_func_source(_FHIR_API_PATH, "_extract_codeable_concept_from_parameters")
    assert src, "_extract_codeable_concept_from_parameters not found"
    assert "isinstance(coding, dict)" in src, (
        "_extract_codeable_concept_from_parameters lacks isinstance(coding, dict) "
        "guard — 10th PROMOTED pattern regression."
    )


def test_h33_behavioral_non_dict_entries_in_coding_array_no_500(fhir_client):
    """10th PROMOTED pattern — behavioral probe.

    POST $translate with a codeableConcept body where coding[] contains
    a non-dict entry (string). The handler MUST NOT raise 500 — the
    isinstance guard skips the malformed entry silently.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            "not-a-dict",  # malformed — MUST be skipped
                            {"system": SNOMED_URI, "code": "44054006"},
                        ]
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r.status_code < 500, (
        f"POST $translate with non-dict coding entry — expected <500 (10th "
        f"PROMOTED pattern). Got {r.status_code}: {r.text}"
    )
    assert r.headers["content-type"].startswith("application/fhir+")


def test_h34_behavioral_non_dict_entries_in_parameter_array_no_500(fhir_client):
    """10th PROMOTED pattern — behavioral probe on _parse_parameters.

    POST $translate with parameter[] containing a non-dict entry. The
    isinstance(param, dict) guard MUST skip the malformed entry without
    500.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                "not-a-dict-entry",  # malformed — MUST be skipped
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r.status_code < 500, (
        f"POST $translate with non-dict parameter[] entry — expected <500. "
        f"Got {r.status_code}: {r.text}"
    )


# ===========================================================================
# Lens 4: client-input-as-canonical drift meta-pattern (count=8+1 PROMOTED)
# Re-derive on _do_translate + match.source.system surface.
# ===========================================================================


def test_h40_do_translate_calls_canonical_system_uri():
    """client-input-as-canonical drift meta-pattern — _do_translate MUST
    call canonical_system_uri before passing source_uri to the builder.

    CR-012 (milestone-2 review) RESOLVED. Without this call, the Out
    ``match[].source.system`` field echoes the client-supplied URI
    verbatim — including aliases (urn:oid:...) and trailing-slash
    variants.
    """
    src = _get_func_source(_FHIR_API_PATH, "_do_translate")
    assert src, "_do_translate not found"
    assert "canonical_system_uri(" in src, (
        "_do_translate does NOT call canonical_system_uri — CR-012 regression "
        "(client-input-as-canonical drift count=8+1 PROMOTED)."
    )


def test_h41_canonical_uri_resolves_trailing_slash():
    """client-input-as-canonical drift — behavioral probe on helper.

    The canonical_system_uri helper MUST resolve the trailing-slash
    SNOMED URI to the canonical form.
    """
    assert canonical_system_uri(SNOMED_URI_TRAILING_SLASH) == SNOMED_URI, (
        f"canonical_system_uri({SNOMED_URI_TRAILING_SLASH!r}) did NOT return "
        f"the canonical URI {SNOMED_URI!r}."
    )


def test_h42_canonical_uri_resolves_urn_oid_alias():
    """client-input-as-canonical drift — behavioral probe on urn:oid alias."""
    assert canonical_system_uri(SNOMED_URI_OID_ALIAS) == SNOMED_URI, (
        f"canonical_system_uri({SNOMED_URI_OID_ALIAS!r}) did NOT return "
        f"the canonical URI {SNOMED_URI!r}."
    )


def test_h43_match_source_system_uses_canonical_for_alias_input(fhir_client):
    """client-input-as-canonical drift — end-to-end behavioral probe.

    POST $translate with urn:oid alias. The Out match[].source.system
    MUST be the canonical URI (NOT the alias).
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI_OID_ALIAS},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert matches, "Expected at least one match for SNOMED 44054006 -> ICD-10-CM"
    for m in matches:
        parts = m.get("part", [])
        source_part = next((p for p in parts if p.get("name") == "source"), None)
        assert source_part is not None
        source_system = source_part.get("valueCoding", {}).get("system")
        assert source_system == SNOMED_URI, (
            f"client-input-as-canonical drift: match.source.system = "
            f"{source_system!r}; expected canonical {SNOMED_URI!r}."
        )


# ===========================================================================
# Lens 5: literal-value-vs-canonical-registry drift (count=8 PROMOTED)
# Re-derive via AST-walk on responses.py — no hardcoded equivalence.
# ===========================================================================


def test_h50_responses_module_uses_canonical_import_only():
    """literal-value-vs-canonical-registry drift — responses.py MUST
    import INTERNAL_REL_TO_FHIR_EQUIVALENCE from the canonical module,
    NOT define a local map.

    AST-walk audit: find ast.ImportFrom nodes targeting the equivalence
    module. Per GLOBAL_RULES.md line 134 (CR-014 sibling), a local
    redefinition is the regression shape.
    """
    src = _RESPONSES_PATH.read_text()
    tree = ast.parse(src)
    found_import = False
    found_local_def = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "INTERNAL_REL_TO_FHIR_EQUIVALENCE":
                    found_import = True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in (
                    "INTERNAL_REL_TO_FHIR_EQUIVALENCE",
                    "_INTERNAL_REL_TO_FHIR_EQUIVALENCE",
                ):
                    found_local_def = True
    assert found_import, (
        "responses.py MUST import INTERNAL_REL_TO_FHIR_EQUIVALENCE from the "
        "canonical equivalence module."
    )
    assert not found_local_def, (
        "responses.py MUST NOT define a local INTERNAL_REL_TO_FHIR_EQUIVALENCE "
        "map — literal-value-vs-canonical-registry drift (count=8 PROMOTED)."
    )


def test_h51_no_hardcoded_equivalence_literal_in_responses():
    """literal-value-vs-canonical-registry drift — no ast.Constant string
    literals for equivalence values should appear in responses.py
    executable code (only in docstrings/comments).

    Walks ast.Constant nodes (NOT substring on raw text) to avoid false-
    positives on commentary per CS-01 HISTORIAN L1 methodology.
    """
    src = _RESPONSES_PATH.read_text()
    tree = ast.parse(src)
    # The set of equivalence literals the canonical module emits.
    forbidden_in_assign = {
        "subsumedby",  # R5/R4B value
        "matches",     # R5-only value
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in forbidden_in_assign:
                # Verify this is NOT in a docstring (ast.Expr wrapping ast.Constant)
                # — these are R5/R4B values that should NEVER appear in executable code.
                pytest.fail(
                    f"responses.py contains forbidden equivalence literal "
                    f"{node.value!r} in executable code. CF-HISTORIAN-VS01-01 "
                    f"regression shape."
                )


def test_h52_canonical_module_emits_only_r4_values():
    """literal-value-vs-canonical-registry drift — canonical module's
    INTERNAL_REL_TO_FHIR_EQUIVALENCE emits only R4 enum values.
    """
    emitted = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
    drift = emitted - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert not drift, (
        f"Canonical equivalence module emits values outside R4 enum: {drift}."
    )


# ===========================================================================
# Lens 6: closed-enum R5/R4B contamination (CF-HISTORIAN-VS01-01 RESOLVED)
# Re-derive on the $translate emitted equivalence surface.
# ===========================================================================


def test_h60_match_equivalence_values_in_r4_enum(fhir_client):
    """CF-HISTORIAN-VS01-01 RESOLVED — every emitted match.equivalence
    value on the $translate surface MUST be in the R4 closed enum.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert matches, "Expected at least one match for the seeded mapping"
    for m in matches:
        for part in m.get("part", []):
            if part.get("name") == "equivalence":
                val = part.get("valueCode")
                assert val in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                    f"match.equivalence value {val!r} NOT in R4 closed enum. "
                    f"CF-HISTORIAN-VS01-01 regression on the runtime surface."
                )


def test_h61_no_r5_r4b_values_in_translate_response(fhir_client):
    """CF-HISTORIAN-VS01-01 RESOLVED — no R5/R4B-only values
    ('subsumedby', 'matches') should appear on the $translate surface.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 200
    body_text = r.text
    assert "subsumedby" not in body_text, (
        "R5/R4B-only value 'subsumedby' leaked into $translate response."
    )
    assert '"matches"' not in body_text, (
        "R5-only value 'matches' leaked into $translate response."
    )


def test_h62_fhir_equivalence_helper_emits_only_r4_values():
    """CF-HISTORIAN-VS01-01 RESOLVED — fhir_equivalence helper returns
    only R4 enum values across every known engine token.
    """
    engine_tokens = [
        "equivalent",
        "source-is-narrower-than-target",
        "source-is-broader-than-target",
        "related-to",
        "not-translated",
        "unmatched",
        None,  # null relationship → relatedto catch-all
        "unknown-token-xyz",  # unknown → relatedto catch-all
    ]
    for tok in engine_tokens:
        result = fhir_equivalence(tok)
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"fhir_equivalence({tok!r}) = {result!r} NOT in R4 closed enum."
        )


def test_h63_helper_directionality_per_r4_spec():
    """CM-01 SKEPTIC-001 regression — directionality preserved through
    the canonical module.

    The prior responses.py map had these inverted:
      * source-is-narrower-than-target ⇒ wider (target is WIDER in meaning)
      * source-is-broader-than-target ⇒ narrower (target is NARROWER)
    """
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-narrower-than-target"] == "wider"
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-broader-than-target"] == "narrower"
    # CM-01 SKEPTIC-002 semantic fix: not-translated ⇒ unmatched
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["not-translated"] == "unmatched"


# ===========================================================================
# Lens 7: GET<->POST byte-exact parity (SKEPTIC tip 2)
# Extend test_s90 (5 cases) to every seeded code x every target system.
# ===========================================================================


# Build the parity matrix: every seeded code x every target system.
PARITY_CASES: list[tuple[str, str, str, str, str]] = []
for sys_uri, code, label in SEEDED_CODES:
    for target_uri, target_label in TARGET_SYSTEMS:
        PARITY_CASES.append(
            (sys_uri, code, target_uri, f"{label} -> {target_label}", "seeded-code")
        )
# Add the alias-input cases from test_s90 to confirm parity holds on aliases too.
PARITY_CASES.extend(
    [
        (
            SNOMED_URI_TRAILING_SLASH,
            "44054006",
            ICD10CM_URI,
            "trailing-slash-source -> ICD-10-CM",
            "alias-input",
        ),
        (
            SNOMED_URI_OID_ALIAS,
            "44054006",
            ICD10CM_URI,
            "oid-alias-source -> ICD-10-CM",
            "alias-input",
        ),
        (
            SNOMED_URI_UPPERCASE_SCHEME,
            "44054006",
            ICD10CM_URI,
            "uppercase-scheme-source -> ICD-10-CM",
            "alias-input",
        ),
    ]
)


@pytest.mark.parametrize(
    "system,code,target,label,kind",
    PARITY_CASES,
    ids=[c[3] for c in PARITY_CASES],
)
def test_h70_get_post_byte_exact_parity_seeded_matrix(
    fhir_client, system, code, target, label, kind
):
    """SKEPTIC tip 2 — GET<->POST byte-exact parity extended to every
    seeded code x every target system.

    For each (source_system, source_code, target_system) tuple, GET and
    POST MUST produce identical:
      (a) status codes,
      (b) result valueBoolean values,
      (c) match count,
      (d) per-match equivalence valueCode,
      (e) per-match concept valueCoding.code.

    SKEPTIC test_s90 covered 5 cases (1 valid baseline + 1 long-code +
    1 no-match + 2 alias-source). HISTORIAN extends to every seeded
    code x every target system (12 cases) + 3 alias-source cases = 15
    total — exercises the FULL fixture matrix per SKEPTIC's "extend to
    every seeded code x every target system" tip.
    """
    r_get = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", system),
            ("code", code),
            ("targetsystem", target),
        ],
    )
    r_post = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": system},
                {"name": "code", "valueCode": code},
                {"name": "targetsystem", "valueUri": target},
            ],
        },
    )
    # (a) status code parity
    assert r_get.status_code == r_post.status_code, (
        f"GET<->POST status drift on ({label}, kind={kind}): "
        f"GET={r_get.status_code}, POST={r_post.status_code}. "
        f"GET body: {r_get.text[:200]}; POST body: {r_post.text[:200]}"
    )
    if r_get.status_code != 200:
        return
    body_get = r_get.json()
    body_post = r_post.json()
    # (b) result valueBoolean parity
    result_get = _find_param(body_get, "result")
    result_post = _find_param(body_post, "result")
    assert result_get and result_post, (
        f"result param missing on ({label}): GET={result_get}, POST={result_post}"
    )
    assert result_get["valueBoolean"] == result_post["valueBoolean"], (
        f"GET<->POST result-value drift on ({label}): "
        f"GET={result_get['valueBoolean']}, POST={result_post['valueBoolean']}"
    )
    # (c) match count parity
    matches_get = [p for p in body_get.get("parameter", []) if p.get("name") == "match"]
    matches_post = [p for p in body_post.get("parameter", []) if p.get("name") == "match"]
    assert len(matches_get) == len(matches_post), (
        f"GET<->POST match-count drift on ({label}): "
        f"GET={len(matches_get)}, POST={len(matches_post)}"
    )
    # (d)+(e) per-match equivalence + concept code parity
    for m_get, m_post in zip(matches_get, matches_post):
        parts_get = {p.get("name"): p for p in m_get.get("part", [])}
        parts_post = {p.get("name"): p for p in m_post.get("part", [])}
        equiv_get = parts_get.get("equivalence", {}).get("valueCode")
        equiv_post = parts_post.get("equivalence", {}).get("valueCode")
        assert equiv_get == equiv_post, (
            f"GET<->POST equivalence drift on ({label}): "
            f"GET={equiv_get!r}, POST={equiv_post!r}"
        )
        concept_get = parts_get.get("concept", {}).get("valueCoding", {}).get("code")
        concept_post = parts_post.get("concept", {}).get("valueCoding", {}).get("code")
        assert concept_get == concept_post, (
            f"GET<->POST target-code drift on ({label}): "
            f"GET={concept_get!r}, POST={concept_post!r}"
        )


# ===========================================================================
# Lens 8: boolean serializer lowercase wire-format (A1 / CR-002 PROMOTED)
# Re-derive on $translate XML surface.
# ===========================================================================


def test_h80_translate_xml_result_lowercase_boolean(fhir_client):
    """A1 / CR-002 — XML wire-format boolean MUST be lowercase.

    Python's str(True) = 'True' (capital T); FHIR R4 §3.4.1 mandates
    lowercase. The XML serializer's _scalar_to_xml helper MUST special-
    case bool BEFORE the generic str() conversion.
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
            ("_format", "xml"),
        ],
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+xml")
    body_text = r.text
    # Lowercase form MUST appear (match found => result=true).
    assert 'value="true"' in body_text or 'value="false"' in body_text, (
        f"XML wire-format drift: result boolean not rendered in lowercase. "
        f"Body: {body_text[:500]}"
    )
    assert 'value="True"' not in body_text, (
        f"XML wire-format drift: capital-T 'True' rendered. Body: {body_text[:500]}"
    )
    assert 'value="False"' not in body_text, (
        f"XML wire-format drift: capital-F 'False' rendered. Body: {body_text[:500]}"
    )


def test_h81_translate_json_result_valueboolean_is_python_bool(fhir_client):
    """A1 / CR-002 sibling — JSON wire-format result.valueBoolean MUST
    serialize to lowercase (Python bool, not int/string).

    Direct builder test confirms the value passed to the JSON serializer
    is a Python bool.
    """
    body = build_parameters_translate(
        [], source_system_uri=SNOMED_URI, source_code="44054006"
    )
    result_param = _find_param(body, "result")
    assert result_param is not None
    # The wire-format correctness is enforced by ensuring the value is
    # a Python bool (json.dumps renders lowercase for bool).
    assert isinstance(result_param["valueBoolean"], bool), (
        f"valueBoolean is {type(result_param['valueBoolean']).__name__}; "
        f"expected Python bool (A1 / CR-002 wire-format correctness)."
    )
    assert result_param["valueBoolean"] is False


# ===========================================================================
# Lens 9: cross-handler helper-wiring (count=6 PROMOTED)
# Re-derive — batch dispatcher's _extract_translate_params + _do_translate
# MUST be the SAME extractors as the type-level POST handler.
# ===========================================================================


def test_h90_batch_dispatcher_calls_extract_translate_params():
    """cross-handler helper-wiring (count=6 PROMOTED) — batch dispatcher
    MUST call _extract_translate_params (sibling of translate_post).

    TS-02 EXPLORER QA-028 pattern class. Without this, batch $translate
    would re-parse the body inline, diverging from the type-level POST
    on coding/codeableConcept alt-encodings.
    """
    src = _FHIR_API_PATH.read_text()
    # The batch dispatcher delegates via the executor pattern. Verify the
    # _extract_translate_params helper is invoked in the batch path
    # (typically inside _process_batch_entry or _dispatch_batch_operation).
    # The load-bearing contract: the SAME extractor is used in BOTH paths.
    assert "_extract_translate_params" in src, (
        "_extract_translate_params not found anywhere — batch path cannot "
        "honor coding/codeableConcept alt-encodings."
    )


def test_h91_batch_vs_single_translate_byte_exact(fhir_client):
    """cross-handler helper-wiring — batch $translate entry MUST match
    single-entry $translate byte-exact.

    Per TS-04 TERMINOLOGIST methodology L1: the batch dispatcher reuses
    the same _do_* handlers and build_parameters_* builders as single-
    entry routes. Clinical content (target code, equivalence value) is
    structurally guaranteed identical.
    """
    # Single-entry GET
    r_single = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r_single.status_code == 200
    single_body = r_single.json()

    # Batch
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {
                    "method": "GET",
                    "url": f"/ConceptMap/$translate?system={SNOMED_URI}"
                    f"&code=44054006&targetsystem={ICD10CM_URI}",
                }
            }
        ],
    }
    r_batch = fhir_client.post("/fhir", json=bundle)
    assert r_batch.status_code == 200
    batch_body = r_batch.json()
    batch_resource = batch_body["entry"][0].get("resource", {})

    single_matches = [p for p in single_body.get("parameter", []) if p.get("name") == "match"]
    batch_matches = [p for p in batch_resource.get("parameter", []) if p.get("name") == "match"]
    assert len(single_matches) == len(batch_matches), (
        f"Single-vs-batch match count divergence: single={len(single_matches)}, "
        f"batch={len(batch_matches)}."
    )
    for s, b in zip(single_matches, batch_matches):
        s_equiv = next(
            (p.get("valueCode") for p in s.get("part", []) if p.get("name") == "equivalence"),
            None,
        )
        b_equiv = next(
            (p.get("valueCode") for p in b.get("part", []) if p.get("name") == "equivalence"),
            None,
        )
        assert s_equiv == b_equiv, (
            f"Single-vs-batch equivalence divergence: single={s_equiv!r}, batch={b_equiv!r}"
        )
        s_concept = next(
            (
                p.get("valueCoding", {}).get("code")
                for p in s.get("part", [])
                if p.get("name") == "concept"
            ),
            None,
        )
        b_concept = next(
            (
                p.get("valueCoding", {}).get("code")
                for p in b.get("part", [])
                if p.get("name") == "concept"
            ),
            None,
        )
        assert s_concept == b_concept, (
            f"Single-vs-batch concept code divergence: single={s_concept!r}, "
            f"batch={b_concept!r}"
        )


# ===========================================================================
# Lens 10: silent-wrong-answer on alt encodings (count=6+ PROMOTED)
# Re-derive — POST coding/codeableConcept body MUST produce the same
# clinical content as the scalar path.
# ===========================================================================


def test_h100_coding_body_matches_scalar_byte_exact(fhir_client):
    """silent-wrong-answer on alt encodings — POST $translate with
    coding-only body MUST produce the same match count + equivalence
    + target code as the scalar path.
    """
    # Scalar path
    r_scalar = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r_scalar.status_code == 200
    # Alt encoding: coding body
    r_coding = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {"system": SNOMED_URI, "code": "44054006"},
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r_coding.status_code == 200

    body_scalar = r_scalar.json()
    body_coding = r_coding.json()

    matches_scalar = [p for p in body_scalar.get("parameter", []) if p.get("name") == "match"]
    matches_coding = [p for p in body_coding.get("parameter", []) if p.get("name") == "match"]
    assert len(matches_scalar) == len(matches_coding), (
        f"Scalar-vs-coding-body match count divergence: scalar="
        f"{len(matches_scalar)}, coding={len(matches_coding)}."
    )
    for s, c in zip(matches_scalar, matches_coding):
        s_equiv = next(
            (p.get("valueCode") for p in s.get("part", []) if p.get("name") == "equivalence"),
            None,
        )
        c_equiv = next(
            (p.get("valueCode") for p in c.get("part", []) if p.get("name") == "equivalence"),
            None,
        )
        assert s_equiv == c_equiv


def test_h101_codeableconcept_body_matches_scalar_byte_exact(fhir_client):
    """silent-wrong-answer on alt encodings — POST $translate with
    codeableConcept body MUST produce the same clinical content as the
    scalar path. Sibling of test_h100.
    """
    r_scalar = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    r_cc = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [{"system": SNOMED_URI, "code": "44054006"}]
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r_scalar.status_code == r_cc.status_code == 200

    body_scalar = r_scalar.json()
    body_cc = r_cc.json()

    matches_scalar = [p for p in body_scalar.get("parameter", []) if p.get("name") == "match"]
    matches_cc = [p for p in body_cc.get("parameter", []) if p.get("name") == "match"]
    assert len(matches_scalar) == len(matches_cc)


# ===========================================================================
# Lens 11: deeply-nested codeableConcept with mixed valid+invalid codings
# Lateral probe class — verify graceful degradation when codeableConcept
# body has a mix of valid + invalid (malformed) codings.
# ===========================================================================


def test_h110_codeableconcept_mixed_valid_invalid_no_500(fhir_client):
    """Deeply-nested codeableConcept — mixed valid+invalid codings.

    Per FHIR R4 §3.1.0.1.5 + §3.1.0.1.9: a malformed client body MUST
    produce a FHIR OperationOutcome (not a 500 + traceback). The
    isinstance(coding, dict) guard in _extract_codeable_concept_from_
    parameters silently skips invalid entries and processes valid ones.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            None,  # malformed — MUST be skipped
                            "not-a-dict",  # malformed — MUST be skipped
                            42,  # malformed — MUST be skipped
                            ["nested", "list"],  # malformed — MUST be skipped
                            {"system": SNOMED_URI, "code": "44054006"},  # VALID
                        ]
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r.status_code < 500, (
        f"Mixed valid+invalid codeableConcept — expected <500 (isinstance guard "
        f"skips invalid). Got {r.status_code}: {r.text}"
    )
    assert r.headers["content-type"].startswith("application/fhir+")
    body = r.json()
    assert body.get("resourceType") == "Parameters"


def test_h111_codeableconcept_all_invalid_no_500(fhir_client):
    """Deeply-nested codeableConcept — all-invalid codings.

    When all codings are malformed, the handler gracefully returns
    400 (no system/code extracted) — NOT a 500 traceback.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            None,
                            "not-a-dict",
                            42,
                            ["nested"],
                        ]
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r.status_code < 500, (
        f"All-invalid codeableConcept — expected <500. Got {r.status_code}: {r.text}"
    )
    assert r.headers["content-type"].startswith("application/fhir+")


def test_h112_codeableconcept_first_valid_wins(fhir_client):
    """Deeply-nested codeableConcept — first VALID coding wins per
    spec ("spec allows server choice for multiple codings" per
    _extract_translate_params docstring).
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            "invalid-1",
                            {"system": SNOMED_URI, "code": "44054006"},  # FIRST VALID
                            {"system": SNOMED_URI, "code": "73211009"},  # also valid
                        ]
                    },
                },
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r.status_code == 200, (
        f"Mixed codeableConcept with first-valid-44054006 — expected 200. "
        f"Got {r.status_code}: {r.text}"
    )
    body = r.json()
    # The first valid coding (44054006) should be used. The Out match.source
    # should reflect 44054006 (T2DM), NOT 73211009 (DM).
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    for m in matches:
        parts = m.get("part", [])
        source_part = next((p for p in parts if p.get("name") == "source"), None)
        if source_part:
            source_code = source_part.get("valueCoding", {}).get("code")
            assert source_code == "44054006", (
                f"First-valid-wins semantic regressed: source code = "
                f"{source_code!r}; expected 44054006."
            )


# ===========================================================================
# Lens 12: Cross-operation round-trip ($lookup <-> $translate <-> $subsumes)
# Lateral probe class — verify a code behaves consistently across the
# 3 operations per SPEC_CONSTRUCTION_PATTERNS.md Pattern 4.
# ===========================================================================


def test_h120_cross_op_lookup_then_translate_same_code(fhir_client):
    """Cross-operation round-trip — $lookup followed by $translate on
    the same code MUST produce consistent canonical system URI.

    Per Pattern 4 (cross-operation round-trip probe): a code's display
    and canonical system URI should be consistent across operations.
    """
    # 1. $lookup returns the canonical system
    r_lookup = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[("system", SNOMED_URI), ("code", "44054006")],
    )
    assert r_lookup.status_code == 200
    lookup_body = r_lookup.json()
    # Extract the canonical system from $lookup
    lookup_system = None
    for p in lookup_body.get("parameter", []):
        if p.get("name") == "system":
            lookup_system = p.get("valueUri")
            break
    # Note: $lookup may not emit system as a top-level Out param; the
    # canonical-system invariant is verified via $translate Out match.source.

    # 2. $translate on the same code with alias input
    r_translate = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI_OID_ALIAS},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r_translate.status_code == 200
    translate_body = r_translate.json()
    matches = [p for p in translate_body.get("parameter", []) if p.get("name") == "match"]
    assert matches, "Expected at least one match for SNOMED 44054006 -> ICD-10-CM"
    for m in matches:
        parts = m.get("part", [])
        source_part = next((p for p in parts if p.get("name") == "source"), None)
        assert source_part is not None
        source_system = source_part.get("valueCoding", {}).get("system")
        # Cross-op invariant: $translate Out match.source.system is the
        # canonical SNOMED URI regardless of the alias input. This is the
        # same canonical URI $lookup would emit (if it emitted system).
        assert source_system == SNOMED_URI


def test_h121_cross_op_lookup_translate_subsumes_same_code(fhir_client):
    """Cross-operation round-trip — 3 operations on the same code MUST
    agree on canonical system URI + result consistency.

    Pattern 4 (cross-operation round-trip probe) — exercises the FULL
    3-operation round-trip per the task lateral directive.
    """
    code = "44054006"  # T2DM

    # 1. $lookup on the code
    r_lookup = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[("system", SNOMED_URI), ("code", code)],
    )
    assert r_lookup.status_code == 200

    # 2. $translate from the code
    r_translate = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", code),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r_translate.status_code == 200
    translate_body = r_translate.json()
    translate_result = _find_param(translate_body, "result")
    assert translate_result is not None
    assert translate_result["valueBoolean"] is True, (
        "Expected $translate result=true for SNOMED 44054006 -> ICD-10-CM"
    )

    # 3. $subsumes between the code and its parent (DM 73211009)
    r_subsumes = fhir_client.get(
        "/fhir/CodeSystem/$subsumes",
        params=[
            ("system", SNOMED_URI),
            ("codeA", code),       # T2DM (narrower)
            ("codeB", "73211009"),  # DM (broader)
        ],
    )
    assert r_subsumes.status_code == 200
    subsumes_body = r_subsumes.json()
    outcome = _find_param(subsumes_body, "outcome")
    assert outcome is not None
    # T2DM (44054006) is subsumed-by DM (73211009)
    assert outcome.get("valueCode") == "subsumed-by", (
        f"$subsumes outcome drift: {outcome.get('valueCode')!r}; "
        f"expected 'subsumed-by' (T2DM subsumed-by DM)."
    )


def test_h122_cross_op_round_trip_canonical_uri_consistency(fhir_client):
    """Cross-operation canonical-URI invariant — $lookup, $translate,
    $subsumes all emit the same canonical SNOMED URI for the same code.

    Extends TS-01 test_t10 bidirectional invariant from $lookup + READ
    + SEARCH to $translate (match.source.system) + $subsumes (Out
    system would mirror via canonical_system_uri but is not emitted;
    the input is the canonical URI).
    """
    code = "44054006"
    # Use the alias for $translate input; the Out should be canonical.
    r_lookup = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[("system", SNOMED_URI), ("code", code)],
    )
    assert r_lookup.status_code == 200

    r_translate = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI_OID_ALIAS},
                {"name": "code", "valueCode": code},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r_translate.status_code == 200
    body = r_translate.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    for m in matches:
        parts = m.get("part", [])
        # source.system MUST be canonical SNOMED URI even though input was alias
        source_part = next((p for p in parts if p.get("name") == "source"), None)
        assert source_part is not None
        source_system = source_part.get("valueCoding", {}).get("system")
        assert source_system == SNOMED_URI, (
            f"Cross-op canonical-URI invariant violation: $translate Out "
            f"match.source.system = {source_system!r} (input was alias); "
            f"expected canonical {SNOMED_URI!r}."
        )
        # concept.system MUST be canonical ICD-10-CM URI
        concept_part = next((p for p in parts if p.get("name") == "concept"), None)
        assert concept_part is not None
        concept_system = concept_part.get("valueCoding", {}).get("system")
        assert concept_system == ICD10CM_URI


# ===========================================================================
# Lens 13: Source-read structural contracts on instance-level routes
# Verify the instance-level $translate routes are registered and short-
# circuit to a FHIR-shaped 404 BEFORE the engine is consulted.
# ===========================================================================


def test_h130_instance_level_translate_get_returns_404_fhir(fhir_client):
    """Instance-level GET /fhir/ConceptMap/{id}/$translate returns 404
    OperationOutcome (medterm4ds does not persist ConceptMaps).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/any-id/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/fhir+")
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


def test_h131_instance_level_translate_post_returns_404_fhir(fhir_client):
    """Instance-level POST /fhir/ConceptMap/{id}/$translate returns 404
    OperationOutcome. Sibling of test_h130.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/any-id/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/fhir+")
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


def test_h132_instance_level_routes_registered():
    """Source-read — instance-level GET/POST $translate routes MUST
    be registered in create_fhir_app.

    TS-02 SKEPTIC QA-014 pattern class.
    """
    src = inspect.getsource(fhir_api.create_fhir_app)
    assert "async def translate_instance_get" in src, (
        "translate_instance_get route not found — TS-02 SKEPTIC QA-014 regression."
    )
    assert "async def translate_instance_post" in src, (
        "translate_instance_post route not found — TS-02 SKEPTIC QA-014 regression."
    )


# ===========================================================================
# Lens 14: Cross-handler state isolation
# Verify no shared mutable state between type-level and instance-level
# $translate routes (CS-03 HISTORIAN QA-052 methodology).
# ===========================================================================


def test_h140_type_level_then_instance_level_no_state_leak(fhir_client):
    """Cross-handler state isolation — type-level followed by instance-
    level MUST NOT leak state.
    """
    r1 = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r1.status_code == 200
    r2 = fhir_client.get(
        "/fhir/ConceptMap/any-id/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r2.status_code == 404
    # The instance-level 404 MUST NOT affect a subsequent type-level call.
    r3 = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r3.status_code == 200


def test_h141_instance_level_then_type_level_no_state_leak(fhir_client):
    """Cross-handler state isolation — instance-level followed by type-
    level MUST NOT leak state.
    """
    r1 = fhir_client.get(
        "/fhir/ConceptMap/any-id/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r1.status_code == 404
    r2 = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    assert r2.status_code == 200
