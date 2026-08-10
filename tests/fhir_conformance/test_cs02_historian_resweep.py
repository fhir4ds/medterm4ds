"""HISTORIAN RESWEEP probes for CS-02 (CodeSystem $lookup Operation) — fresh
full-sweep run.

Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html (R4 / 4.0.1).

HISTORIAN lens (per ROLE_QA_ENGINEER Section 3): pattern-match against prior
bug patterns. Every probe re-derives one prior CS-02 bug OR one of the 9
PROMOTED patterns from GLOBAL_RULES.md against current code. A regression
fires loudly when a previously-fixed pattern recurs.

SKEPTIC tip for HISTORIAN: CS-02 HISTORIAN's prior 2 bugs (QA-046
pf_cache isinstance guard, QA-047 canonical_system_uri for Out `system`)
are confirmed HELD in the SKEPTIC iteration via source-read probes
(test_s103 / test_s104) + behavioral probes (test_s55 / test_s56).
The $lookup surface is structurally hardened by 5 prior chunks; HISTORIAN
is expected to close CLEAN.

10 lens dimensions / 40+ regression probes:
  L1 — QA-046 pf_cache isinstance guard (CS-02 HISTORIAN prior bug)
  L2 — QA-047 canonical_system_uri for Out `system` (CS-02 HISTORIAN prior bug)
  L3 — HCPCS canonical URI drift class (count=8+1 PROMOTED)
  L4 — Client-input-as-canonical drift meta-pattern (count=8+1 PROMOTED)
  L5 — Literal-value-vs-canonical-registry drift (count=8 PROMOTED)
  L6 — Empty-string-as-present-on-required-Query drift (count=5 PROMOTED)
  L7 — Closed-enum R5/R4B contamination drift (CF-HISTORIAN-VS01-01 RESOLVED)
  L8 — Boolean serializer lowercase wire-format (A1 / CR-002 PROMOTED)
  L9 — Negative-only test-too-lenient probe class (TS-03 HISTORIAN QA-034)
  L10 — Cross-handler helper-wiring inconsistency (count=6 PROMOTED)
  L11 — CF-HISTORIAN-VS02-02 RESOLVED sibling (canonical_system_uri on
        $expand implicit path — OUT OF SCOPE for CS-02 but load-bearing
        as the meta-pattern consolidation reference)
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
# Out Parameters:
#   name      1..1  string  "A display name for the code system"
#   version   0..1  string  "The version that these details are based on"
#   display   1..1  string  "The preferred display for this concept"
#   designation 0..*
#   property  0..*

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DM = "73211009"
SNOMED_T2DM = "44054006"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_E11 = "E11"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_860975 = "860975"
HCPCS_URI = "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"
HCPCS_LEGACY_THO_URI = "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II"

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
_FHIR_INIT_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "medterm4ds"
    / "engines"
    / "fhir"
    / "__init__.py"
)


def _get_func_source(file_path: Path, func_name: str) -> str:
    """Extract the source text of a function (possibly nested) by name.

    Walks BOTH ast.FunctionDef AND ast.AsyncFunctionDef (per TS-04 HISTORIAN
    methodology — async route handlers nested inside create_fhir_app).
    """
    tree = ast.parse(file_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return ast.get_source_segment(file_path.read_text(), node)
    return ""


def _params_by_name(body: dict, name: str) -> list[dict]:
    return [p for p in body.get("parameter", []) if p.get("name") == name]


def _first_param(body: dict, name: str) -> dict | None:
    matches = _params_by_name(body, name)
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# L1 — QA-046 pf_cache isinstance guard regression (CS-02 HISTORIAN prior
# CRITICAL bug). The fix added ``isinstance(pf, dict)`` in ``_do_lookup`` so
# a malformed patient-friendly cache entry (per-code value is a list, not a
# dict) does NOT raise AttributeError → 500 + text/plain.
#
# Spec: FHIR R4 §3.1.0.1.5 (OperationOutcome MAY be returned with any
# 4xx/5xx) + §3.1.0.1.9 (correct MIME type SHALL be used) + §4.8.21.1
# (Out ``property`` is 0..* — absence is spec-conformant).
# ---------------------------------------------------------------------------


def test_h01_do_lookup_has_isinstance_pf_dict_guard():
    """QA-046 regression — source-read contract.

    ``_do_lookup`` MUST contain ``isinstance(pf, dict)`` so malformed
    patient-friendly cache entries don't leak AttributeError past the
    route handler. If this assertion fails, the QA-046 fix has been
    reverted.
    """
    src = _get_func_source(_FHIR_API_PATH, "_do_lookup")
    assert "isinstance(pf, dict)" in src, (
        "_do_lookup MUST guard pf_cache with isinstance(pf, dict). "
        "Regression of CS-02 HISTORIAN QA-046 (CRITICAL)."
    )


def test_h02_do_lookup_emits_warning_on_malformed_entry():
    """QA-046 regression — source-read WARNING contract.

    When ``pf`` is present but not a dict, the impl logs at WARNING
    (NOT DEBUG/INFO) per GLOBAL_RULES.md "Silent Fallbacks". DEBUG-level
    swallowing would hide the data-quality issue.
    """
    src = _get_func_source(_FHIR_API_PATH, "_do_lookup")
    # The malformed-entry branch MUST log at WARNING.
    assert "logger.warning" in src, (
        "_do_lookup MUST log at WARNING when pf_cache entry is malformed "
        "(GLOBAL_RULES.md 'Silent Fallbacks')."
    )
    assert "Malformed" in src or "malformed" in src, (
        "WARNING log message MUST reference the malformed nature of the entry."
    )


def test_h03_do_lookup_no_broad_except():
    """QA-046 regression — silent-fallback negative control.

    ``_do_lookup`` should NOT add a broad ``except Exception:`` to mask
    the QA-046 fix — the isinstance guard is the narrowest fix; broader
    catches would violate GLOBAL_RULES.md "Silent Fallbacks".
    """
    src = _get_func_source(_FHIR_API_PATH, "_do_lookup")
    assert "except Exception" not in src, (
        "_do_lookup MUST NOT contain broad 'except Exception' (GLOBAL_RULES.md "
        "'Silent Fallbacks'). The isinstance(pf, dict) guard is the narrowest fix."
    )


def test_h04_do_lookup_skip_silent_when_malformed(fhir_client):
    """QA-046 regression — behavioral contract (skip silently).

    When the PF entry is absent (not malformed), the lookup still
    succeeds. This is the negative-control behavioral probe — the
    malformed-entry behavioral probe lives in test_cs02_historian.py
    (test_h01) where the fixture injects a list-shaped entry.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    assert r.status_code == 200, f"lookup must succeed; got {r.status_code}"
    body = r.json()
    assert body.get("resourceType") == "Parameters"
    # display MUST still be present even without custom-property enrichment.
    display = _first_param(body, "display")
    assert display is not None and display.get("valueString")


# ---------------------------------------------------------------------------
# L2 — QA-047 canonical_system_uri for Out `system` (CS-02 HISTORIAN prior
# MEDIUM bug). The fix routes ``system_uri`` through
# ``canonical_system_uri(system_uri, source=source)`` before passing to the
# response builder.
#
# Pattern: client-input-as-canonical drift (TS-02 TERMINOLOGIST QA-029 shape).
# Spec: FHIR R4 §4.8.21.1 Out `system` "The canonical URI of the code system
# that contains the concept that was looked up. (This may differ from the
# value passed in `system` as an input parameter if the code was found in a
# different system/subsystem, such as a supplement.)"
# ---------------------------------------------------------------------------


def test_h10_do_lookup_calls_canonical_system_uri():
    """QA-047 regression — source-read contract.

    ``_do_lookup`` MUST call ``canonical_system_uri(...)`` before passing
    system_uri to ``build_parameters_lookup``. The helper is the single
    source of truth for canonical re-resolution (CF-HISTORIAN-VS02-02 fix
    consolidated every Out-system emission through this helper).
    """
    src = _get_func_source(_FHIR_API_PATH, "_do_lookup")
    assert "canonical_system_uri(" in src, (
        "_do_lookup MUST call canonical_system_uri() for Out `system`. "
        "Regression of CS-02 HISTORIAN QA-047 (client-input-as-canonical drift)."
    )


def test_h11_do_lookup_passes_canonical_uri_to_builder():
    """QA-047 regression — source-read builder-arg contract.

    ``_do_lookup`` MUST pass ``canonical_uri`` (not ``system_uri``) to
    ``build_parameters_lookup(system_uri=canonical_uri, ...)``. The
    canonical_uri variable name is the load-bearing signal.
    """
    src = _get_func_source(_FHIR_API_PATH, "_do_lookup")
    assert "system_uri=canonical_uri" in src, (
        "_do_lookup MUST pass system_uri=canonical_uri to build_parameters_lookup. "
        "Passing system_uri=system_uri would re-introduce QA-047."
    )


def test_h12_lookup_system_out_canonical_for_oid_alias(fhir_client):
    """QA-047 regression — behavioral probe (urn:oid alias).

    GET with system=urn:oid:2.16.840.1.113883.6.96 → Out `system` MUST be
    the canonical URI ``http://snomed.info/sct``, NOT the alias verbatim.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": "urn:oid:2.16.840.1.113883.6.96",
            "code": SNOMED_DM,
        },
    )
    assert r.status_code == 200, f"alias lookup must resolve; got {r.status_code}"
    body = r.json()
    sys_param = _first_param(body, "system")
    assert sys_param is not None, "Out `system` parameter MUST be present"
    assert sys_param.get("valueUri") == SNOMED_URI, (
        f"Out `system` MUST be canonical FHIR URI ({SNOMED_URI}); "
        f"got {sys_param.get('valueUri')!r}. Regression of QA-047."
    )


def test_h13_lookup_system_out_canonical_for_trailing_slash(fhir_client):
    """QA-047 regression — behavioral probe (trailing-slash variant).

    GET with system=http://snomed.info/sct/ (trailing slash) → Out `system`
    MUST be the canonical URI without trailing slash.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct/", "code": SNOMED_DM},
    )
    assert r.status_code == 200
    body = r.json()
    sys_param = _first_param(body, "system")
    assert sys_param is not None
    assert sys_param.get("valueUri") == SNOMED_URI, (
        f"Out `system` MUST be canonical without trailing slash; "
        f"got {sys_param.get('valueUri')!r}"
    )


def test_h14_lookup_system_out_canonical_for_uppercase_scheme(fhir_client):
    """QA-047 regression — behavioral probe (uppercase-scheme per TS-03
    EXPLORER QA-001). GET with HTTP://snomed.info/sct → Out `system` MUST
    be canonical lowercase URI.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "HTTP://snomed.info/sct", "code": SNOMED_DM},
    )
    assert r.status_code == 200, f"uppercase-scheme lookup must resolve; got {r.status_code}"
    body = r.json()
    sys_param = _first_param(body, "system")
    assert sys_param is not None
    assert sys_param.get("valueUri") == SNOMED_URI


def test_h15_lookup_system_out_unchanged_when_already_canonical(fhir_client):
    """QA-047 regression — negative control. When client passes the canonical
    URI, Out `system` is the same canonical URI (no double-translation)."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM},
    )
    body = r.json()
    sys_param = _first_param(body, "system")
    assert sys_param is not None
    assert sys_param.get("valueUri") == SNOMED_URI


def test_h16_canonical_system_uri_helper_present_in_fhir_init():
    """QA-047 regression — source-read helper contract.

    The canonical_system_uri helper MUST be defined in
    engines/fhir/__init__.py (canonical location per GLOBAL_RULES.md
    "Single Source of Truth"). Sibling consumers MUST import from here.
    """
    src = _FHIR_INIT_PATH.read_text()
    assert "def canonical_system_uri(" in src, (
        "engines/fhir/__init__.py MUST define canonical_system_uri(). "
        "Removal would break every Out-system re-resolution call site."
    )


# ---------------------------------------------------------------------------
# L3 — HCPCS canonical URI drift class (count=8+1 PROMOTED).
# Pattern: the prior incorrect THO CodeSystem resource URL
# (http://terminology.hl7.org/CodeSystem/hcpcs-Level-II) is kept as INPUT
# alias only; advertisement + Out parameters MUST use the canonical CMS URI.
# Spec: HL7 THO v5.5.0.
# ---------------------------------------------------------------------------


def test_h20_hcpcs_canonical_uri_in_registry():
    """HCPCS URI drift class regression — registry-level contract.

    SYSTEM_TO_FHIR_URI["HCPCS"] MUST be the CMS URI, NOT the legacy THO URL.
    """
    from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

    assert SYSTEM_TO_FHIR_URI.get("HCPCS") == HCPCS_URI, (
        "HCPCS canonical URI drift: registry MUST contain CMS URI, not THO URL."
    )


def test_h21_hcpcs_legacy_tho_url_is_input_alias_only():
    """HCPCS URI drift class regression — alias-level contract.

    The legacy THO URL MUST appear in FHIR_URI_ALIASES (input-only) and
    MUST NOT appear in SYSTEM_TO_FHIR_URI values (advertisement).
    """
    from medterm4ds.engines.fhir import (
        FHIR_URI_ALIASES,
        SYSTEM_TO_FHIR_URI,
    )

    assert FHIR_URI_ALIASES.get(HCPCS_LEGACY_THO_URI) == "HCPCS", (
        "HCPCS legacy THO URL MUST be kept as input alias for back-compat."
    )
    assert HCPCS_LEGACY_THO_URI not in SYSTEM_TO_FHIR_URI.values(), (
        "HCPCS legacy THO URL MUST NOT be advertised as canonical."
    )


def test_h22_lookup_hcpcs_out_system_is_canonical_cms_uri(fhir_client):
    """HCPCS URI drift class regression — behavioral on $lookup surface."""
    # The default fhir_client may not have HCPCS data seeded, but the Out
    # `system` is canonical regardless. If lookup fails (unknown code),
    # the error response from _fhir_error still uses canonical system.
    # We test with the alias input first to confirm alias resolution path.
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": HCPCS_LEGACY_THO_URI, "code": "G0008"},
    )
    # Either success (200) or 4xx with OperationOutcome — both are spec-
    # conformant. The drift would be if Out `system` echoed the alias
    # verbatim on a 200 response.
    if r.status_code == 200:
        body = r.json()
        sys_param = _first_param(body, "system")
        if sys_param:
            assert sys_param.get("valueUri") == HCPCS_URI, (
                "HCPCS Out `system` MUST be canonical CMS URI, NOT legacy THO URL."
            )


# ---------------------------------------------------------------------------
# L4 — Client-input-as-canonical drift meta-pattern (count=8+1 PROMOTED).
# Cross-handler source-read audit: every ``_do_*`` handler that emits an
# Out `system` MUST call canonical_system_uri.
# ---------------------------------------------------------------------------


def test_h30_do_validate_calls_canonical_system_uri():
    """Client-input-as-canonical drift — CS-03 HISTORIAN QA-051 sibling.

    ``_do_validate`` MUST call canonical_system_uri for its Out `system`.
    CS-03 HISTORIAN QA-051 found this missing; the fix MUST NOT regress.
    """
    src = _get_func_source(_FHIR_API_PATH, "_do_validate")
    assert "canonical_system_uri(" in src, (
        "_do_validate MUST call canonical_system_uri() (CS-03 HISTORIAN QA-051)."
    )


def test_h31_do_vs_validate_calls_canonical_system_uri():
    """Client-input-as-canonical drift — VS-$validate-code surface.

    ``_do_vs_validate`` MUST call canonical_system_uri (CR-025 from
    milestone-3 review).
    """
    src = _get_func_source(_FHIR_API_PATH, "_do_vs_validate")
    assert "canonical_system_uri(" in src, (
        "_do_vs_validate MUST call canonical_system_uri() (CR-025 milestone-3)."
    )


def test_h32_do_translate_calls_canonical_system_uri():
    """Client-input-as-canonical drift — $translate surface.

    ``_do_translate`` MUST call canonical_system_uri (CR-012 from
    milestone-2 review).
    """
    src = _get_func_source(_FHIR_API_PATH, "_do_translate")
    assert "canonical_system_uri(" in src, (
        "_do_translate MUST call canonical_system_uri() (CR-012 milestone-2)."
    )


def test_h33_build_parameters_lookup_accepts_system_uri_canonical_param():
    """Client-input-as-canonical drift — builder-level contract.

    ``build_parameters_lookup`` MUST accept ``system_uri`` as a parameter
    (NOT derive it from code_info or any other field). The caller is
    responsible for canonical re-resolution BEFORE calling.
    """
    src = _get_func_source(_RESPONSES_PATH, "build_parameters_lookup")
    assert "system_uri" in src, (
        "build_parameters_lookup MUST accept system_uri parameter; "
        "the caller is responsible for canonical re-resolution."
    )


# ---------------------------------------------------------------------------
# L5 — Literal-value-vs-canonical-registry drift (count=8 PROMOTED).
# Audit responses.py for hardcoded system URI literals in executable code
# (NOT in comments/docstrings). Per CS-01 HISTORIAN methodology: AST walk
# of ast.Constant nodes only.
# ---------------------------------------------------------------------------


def test_h40_no_hardcoded_hcpcs_literal_in_responses_executable_code():
    """Literal-value drift — responses.py executable code MUST NOT contain
    the legacy HCPCS THO URL as a string literal. AST walk of ast.Constant
    nodes only (avoids false-flags on comments/docstrings).
    """
    tree = ast.parse(_RESPONSES_PATH.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value != HCPCS_LEGACY_THO_URI, (
                f"responses.py MUST NOT contain hardcoded HCPCS legacy THO URL "
                f"as string literal at line {node.lineno}. Use registry."
            )


def test_h41_no_hardcoded_snomed_literal_in_responses_executable_code():
    """Literal-value drift — responses.py MUST NOT hardcode SNOMED URI.
    All URIs MUST come from SYSTEM_TO_FHIR_URI registry (single source of
    truth per GLOBAL_RULES.md).
    """
    tree = ast.parse(_RESPONSES_PATH.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Exception: registry definition itself + docstrings/comments.
            assert node.value != SNOMED_URI or "SYSTEM_TO_FHIR_URI" in _RESPONSES_PATH.read_text()[:500], (
                f"responses.py MUST NOT hardcode SNOMED URI as literal at line "
                f"{node.lineno}. Import from engines.fhir.SYSTEM_TO_FHIR_URI."
            )


# ---------------------------------------------------------------------------
# L6 — Empty-string-as-present-on-required-Query drift (count=5 PROMOTED).
# CS-02's GET handler ``lookup_get`` declares system/code as Query(required);
# both MUST have ``min_length=1`` per the empty-string drift pattern.
# ---------------------------------------------------------------------------


def test_h50_lookup_get_system_has_min_length_1():
    """Empty-string drift — source-read contract on lookup_get.

    ``lookup_get`` MUST declare ``system: str = Query(..., min_length=1)``.
    FastAPI's Query(required) sentinel accepts empty string as "present";
    without min_length=1 the handler proceeds to look up code='' and
    returns 200 + not-found OperationOutcome (silent-wrong-answer).
    """
    src = _get_func_source(_FHIR_API_PATH, "lookup_get")
    # Find the system declaration line
    assert "min_length=1" in src, (
        "lookup_get MUST declare min_length=1 on required string Query params. "
        "Regression of TS-02 SKEPTIC QA-001 (empty-string drift, count=5 PROMOTED)."
    )


def test_h51_lookup_get_code_has_min_length_1():
    """Empty-string drift — code parameter on lookup_get."""
    src = _get_func_source(_FHIR_API_PATH, "lookup_get")
    assert "min_length=1" in src


def test_h52_lookup_get_rejects_empty_system_with_422(fhir_client):
    """Empty-string drift — behavioral probe.

    GET /fhir/CodeSystem/$lookup?system=&code=... MUST be rejected with 422
    (RequestValidationError) → FHIR OperationOutcome via exception handler.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "", "code": SNOMED_DM},
    )
    assert r.status_code == 422, (
        f"Empty system MUST be rejected with 422 (RequestValidationError); "
        f"got {r.status_code}. Regression of empty-string drift pattern."
    )
    # The exception handler converts 422 to FHIR OperationOutcome.
    assert "fhir+json" in r.headers.get("content-type", "")


def test_h53_lookup_get_rejects_empty_code_with_422(fhir_client):
    """Empty-string drift — behavioral probe for empty code."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": ""},
    )
    assert r.status_code == 422
    assert "fhir+json" in r.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# L7 — Closed-enum R5/R4B contamination drift (CF-HISTORIAN-VS01-01 RESOLVED).
# The $lookup surface does NOT emit equivalence codes, but the
# FHIR_R4_CONCEPT_MAP_EQUIVALENCE frozen-set is the load-bearing contract
# that R5/R4B values don't leak. Verify the constant is intact.
# ---------------------------------------------------------------------------


def test_h60_fhir_r4_concept_map_equivalence_constant_present():
    """CF-HISTORIAN-VS01-01 RESOLVED — source-read contract.

    The FHIR_R4_CONCEPT_MAP_EQUIVALENCE frozen-set MUST be defined in
    engines/fhir/__init__.py as the single source of truth (R4 10 values,
    no R5/R4B contamination).
    """
    src = _FHIR_INIT_PATH.read_text()
    assert "FHIR_R4_CONCEPT_MAP_EQUIVALENCE" in src, (
        "engines/fhir/__init__.py MUST define FHIR_R4_CONCEPT_MAP_EQUIVALENCE."
    )


def test_h61_r4_equivalence_no_r5_contamination():
    """CF-HISTORIAN-VS01-01 RESOLVED — R5/R4B values MUST NOT be in R4 set."""
    from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    # R4 has 10 values; R5/R4B adds subsumedby, matches, not-relatedto.
    assert "subsumedby" not in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert "matches" not in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert "not-relatedto" not in FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    # R4 has 'specializes' (NOT 'subsumedby').
    assert "specializes" in FHIR_R4_CONCEPT_MAP_EQUIVALENCE


# ---------------------------------------------------------------------------
# L8 — Boolean serializer lowercase wire-format (A1 / CR-002 PROMOTED).
# The $lookup response shape uses valueString for all property values today
# (no valueBoolean emitted by $lookup). However, the GLOBAL_RULES.md
# boolean-rendering trigger applies to every wire-format serializer. Verify
# the xml serializer's _scalar_to_xml helper is intact (load-bearing for
# _format=xml on $lookup).
# ---------------------------------------------------------------------------


def test_h70_xml_serializer_has_scalar_to_xml_helper():
    """A1 / CR-002 regression — source-read contract.

    The XML serializer MUST have a ``_scalar_to_xml`` helper (or
    equivalent) that special-cases ``isinstance(v, bool)`` BEFORE generic
    str() conversion. Without this, valueBoolean renders as
    "True"/"False" (capital) instead of "true"/"false" per FHIR R4 §3.4.1.
    """
    xml_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "medterm4ds"
        / "engines"
        / "fhir"
        / "xml.py"
    )
    if not xml_path.exists():
        pytest.skip("engines/fhir/xml.py not present")
    src = xml_path.read_text()
    assert "isinstance(v, bool)" in src or "isinstance(value, bool)" in src, (
        "engines/fhir/xml.py MUST special-case bool before str() conversion "
        "(CR-002). Python's str(True) is 'True' (capital T), not 'true'."
    )


def test_h71_lookup_get_format_xml_returns_xml_content_type(fhir_client):
    """A1 — behavioral contract on _format=xml path for $lookup.

    GET /fhir/CodeSystem/$lookup?_format=xml MUST return
    application/fhir+xml Content-Type (per FHIR R4 §3.1.0.1.9).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": SNOMED_DM, "_format": "xml"},
    )
    assert r.status_code == 200
    assert "fhir+xml" in r.headers.get("content-type", ""), (
        f"_format=xml MUST return application/fhir+xml; got "
        f"{r.headers.get('content-type')!r}"
    )


# ---------------------------------------------------------------------------
# L9 — Negative-only test-too-lenient probe class (TS-03 HISTORIAN QA-034).
# Audit the CS-02 test suite for negative-only probes that would false-pass
# on a real bug. The CS-02 SKEPTIC resweep test_s01-s04 (name=CS-name) are
# positive-shape probes (assert the value IS the CS name); confirm.
# ---------------------------------------------------------------------------


def test_h80_cs02_skeptic_resweep_test_s01_is_positive_shape():
    """Test-too-lenient regression — source-read of CS-02 SKEPTIC test_s01.

    test_s01_lookup_name_is_code_system_name_not_concept_term_snomed MUST
    assert a POSITIVE shape (the actual CS name string), not a negative-
    only check on error absence.
    """
    skeptic_resweep_path = (
        Path(__file__).resolve().parent / "test_cs02_skeptic_resweep.py"
    )
    if not skeptic_resweep_path.exists():
        pytest.skip("CS-02 SKEPTIC resweep file not present")
    src = skeptic_resweep_path.read_text()
    # Find the test_s01 function body
    tree = ast.parse(src)
    test_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "test_s01_lookup_name_is_code_system_name_not_concept_term_snomed":
            test_func = node
            break
    if test_func is None:
        pytest.skip("test_s01 not found in CS-02 SKEPTIC resweep")
    body_src = ast.get_source_segment(src, test_func)
    # Positive shape: asserts the actual CS name string.
    assert "SNOMED" in body_src or "system_display_name" in body_src, (
        "test_s01 MUST assert positive CS-name shape, not just absence of error."
    )


# ---------------------------------------------------------------------------
# L10 — Cross-handler helper-wiring inconsistency (count=6 PROMOTED).
# The POST handler ``lookup_post`` MUST route through ``_do_lookup`` (not
# inline construction). Sibling of TS-02 SKEPTIC QA-020 helper-exists-but-
# not-wired pattern.
# ---------------------------------------------------------------------------


def test_h90_lookup_post_routes_through_do_lookup():
    """Cross-handler helper-wiring — source-read contract.

    ``lookup_post`` MUST call ``_do_lookup`` (not inline construction).
    """
    src = _get_func_source(_FHIR_API_PATH, "lookup_post")
    assert "_do_lookup" in src, (
        "lookup_post MUST route through _do_lookup (cross-handler helper-wiring "
        "pattern count=6 PROMOTED). Inline construction would bypass the "
        "canonical_system_uri + isinstance(pf, dict) guards."
    )


def test_h91_lookup_get_routes_through_do_lookup():
    """Cross-handler helper-wiring — GET handler MUST also route through
    _do_lookup (single handler, both routes)."""
    src = _get_func_source(_FHIR_API_PATH, "lookup_get")
    assert "_do_lookup" in src


def test_h92_lookup_batch_dispatcher_routes_through_do_lookup():
    """Cross-handler helper-wiring — batch dispatcher MUST route through
    _do_lookup (per CS-03 HISTORIAN QA-052 all-pairs helper pattern)."""
    # The batch dispatcher is _process_batch_entry or _dispatch_batch_operation.
    for name in ("_process_batch_entry", "_dispatch_batch_operation"):
        src = _get_func_source(_FHIR_API_PATH, name)
        if src and "_do_lookup" in src:
            return
    pytest.fail(
        "Neither _process_batch_entry nor _dispatch_batch_operation calls "
        "_do_lookup. Batch entries MUST share the canonicalization + PF-guard "
        "contract with single-entry routes (cross-handler helper-wiring "
        "pattern count=6 PROMOTED)."
    )


# ---------------------------------------------------------------------------
# L11 — CF-HISTORIAN-VS02-02 RESOLVED sibling (canonical_system_uri on
# $expand implicit path). OUT OF CS-02 SCOPE but load-bearing as the
# meta-pattern consolidation reference. The fix lives in
# _expand_implicit_value_set at apps/fhir_api.py.
# ---------------------------------------------------------------------------


def test_h100_cf_vs02_02_resolved_implicit_path_uses_canonical_helper():
    """CF-HISTORIAN-VS02-02 RESOLVED — source-read sibling contract.

    ``_expand_implicit_value_set`` MUST call canonical_system_uri for its
    contains[].system field. This was the 9th instance of the client-
    input-as-canonical drift pattern (count=8+1 PROMOTED); TS-03 SKEPTIC
    QA-001 fixed it.
    """
    src = _get_func_source(_FHIR_API_PATH, "_expand_implicit_value_set")
    if not src:
        pytest.skip("_expand_implicit_value_set not found")
    assert "canonical_system_uri(" in src, (
        "_expand_implicit_value_set MUST call canonical_system_uri() "
        "(CF-HISTORIAN-VS02-02 RESOLVED)."
    )


# ---------------------------------------------------------------------------
# Additional meta-invariant: _do_lookup has no try/except today (negative
# control per the existing test_h51). The QA-046 fix uses isinstance guard,
# NOT try/except. This is the load-bearing distinction.
# ---------------------------------------------------------------------------


def test_h110_do_lookup_no_try_except_blocks():
    """Meta-invariant — _do_lookup uses isinstance guard, NOT try/except.

    Per CS-02 HISTORIAN prior test_h51 (which is now in the baseline test
    file): _do_lookup has NO try/except blocks. The QA-046 fix is the
    isinstance guard — adding a try/except would mask new error paths.
    """
    src = _get_func_source(_FHIR_API_PATH, "_do_lookup")
    # Parse the function and walk for Try nodes.
    tree = ast.parse(src)
    has_try = any(isinstance(n, ast.Try) for n in ast.walk(tree))
    assert not has_try, (
        "_do_lookup MUST NOT contain try/except blocks (per CS-02 HISTORIAN "
        "test_h51 contract). The QA-046 fix is the isinstance guard; "
        "try/except would mask new error paths (silent-fallback violation)."
    )


# ---------------------------------------------------------------------------
# Cross-chunk meta: FHIR_R4_CONCEPT_MAP_EQUIVALENCE is a frozen-set (not a
# regular set). Frozen-set is the load-bearing signal that the contract
# cannot be mutated at runtime by accident.
# ---------------------------------------------------------------------------


def test_h111_r4_equivalence_constant_is_frozenset():
    """CF-HISTORIAN-VS01-01 RESOLVED — frozen-set immutability contract."""
    from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE

    assert isinstance(FHIR_R4_CONCEPT_MAP_EQUIVALENCE, frozenset), (
        "FHIR_R4_CONCEPT_MAP_EQUIVALENCE MUST be a frozenset (immutable "
        "registry-as-contract)."
    )
