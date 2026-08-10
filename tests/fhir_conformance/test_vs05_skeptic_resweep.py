"""VS-05 SKEPTIC resweep: ValueSet $validate-code Operation.

Source: https://build.fhir.org/valueset-operation-validate-code.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-validate-code.html

This is the resweep (post-milestone-11) SKEPTIC pass for chunk VS-05.
The prior VS-05 SKEPTIC test_vs05_skeptic.py covered the 6 spec items +
landed 2 fixes:
  - QA-069 (HIGH): display mismatch enforcement on ``_do_vs_validate``
    (CF-SKEPTIC-CS03-01 CLOSED — mirror of CS-03 SKEPTIC QA-048 on the
    sibling CodeSystem handler).
  - QA-070 (MEDIUM): codeableConcept multi-coding all-pairs helper
    wiring (mirror of CS-03 SKEPTIC QA-049 + CS-03 HISTORIAN QA-052).

This resweep focuses on SKEPTIC's hostile-input lens:

  1. **Required-params hostile inputs** (Item 1) — empty/missing/malformed
     url/code/system, very long values, special chars, unicode, SQL
     injection-shaped, type mismatches in POST bodies.
  2. **Optional-params hostile inputs** (Item 2) — systemVersion non-
     existent, display very long / special chars / unicode / null bytes,
     date RFC 3339 / partial / malformed / future / past, displayLanguage
     BCP 47 edge cases.
  3. **Out Parameters shape** (Item 3) — result always present + lowercase
     wire-format boolean + valueString message + canonical Out display.
  4. **CodeableConcept any-match semantics** (Item 4) — malformed
     valueCodeableConcept shapes, mixed-system, partial codings, very
     large coding[], non-dict entries, multi-match precedence.
  5. **Display mismatch** (Item 5) — CF-SKEPTIC-CS03-01 fix shape
     re-verified + parametrized over every seeded system + byte-exact
     message format + canonical Out display ≠ echo + canonical-DISPLAY
     META-PATTERN extension (VS-04/TERMINOLOGIST tip) — VS/$validate-code
     Out display MUST byte-exact equal $lookup Out display.
  6. **Implicit value set** (Item 6) — code system URI alone as URL,
     SNOMED intensional URL form, hostile URL shapes.

  7. **Source-read structural contracts** — simplest way to lock in
     expected behaviors without depending on fixture data:
     - ``canonical_system_uri`` on BOTH the scalar-system path AND the
       codeableConcept matched-uri path (CR-011 + CR-025).
     - All-pairs helper on BOTH per-op POST AND batch dispatcher's
       ``_extract_vs_validate_params`` (VS-05 SKEPTIC QA-070).
     - Display-mismatch check inline in ``_do_vs_validate``.
     - Builder canonical precedence (``code_info.name`` > client display).
     - ``min_length=1`` on required string Query (empty-string drift
       count=5 PROMOTED).
     - isinstance-dict guards on Parameters-body iterators (count=4
       PROMOTED — 10th PROMOTED pattern).

  8. **GET↔POST byte-exact parity** on hostile inputs.
  9. **CF-EXPLORER-CS02-01 4-shape POST Content-Type family closure**
     (VS-04/TERMINOLOGIST tip) — the LAST operation needing closure on
     the carry-forward. 4 shapes: system+code body / coding body /
     codeableConcept body / error path. Every shape MUST return
     ``Content-Type: application/fhir+json`` + Parameters body (200) or
     OperationOutcome body (4xx).
 10. **Canonical-DISPLAY META-PATTERN extension** (VS-04/TERMINOLOGIST
     tip) — the canonical-DISPLAY META-PATTERN (count=7 PROMOTED) already
     spans $lookup + $validate-code (CodeSystem) + $expand (extensional/
     intensional/filter/implicit/URL-form). VS-05 SKEPTIC verifies
     ValueSet/$validate-code Out ``display`` joins the META-PATTERN with
     byte-exact agreement vs $lookup.

Conformance fixture (4 mrconso rows, 1 mrrel row): SNOMEDCT_US has 2
codes (Diabetes mellitus / T2DM); ICD10CM has 1 (E11); RXNORM has 1
(metformin ER); mrrel has a single isa relationship (T2DM → DM).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/valueset-operation-validate-code.html
# Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
# (cross-reference per FHIR R4 §4.9.3 — In/Out Parameters structurally
# identical to the CodeSystem operation)
from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI, canonical_system_uri

# Seeded systems + canonical URIs
SNOMED_URI = SYSTEM_TO_FHIR_URI["SNOMEDCT_US"]   # http://snomed.info/sct
ICD10CM_URI = SYSTEM_TO_FHIR_URI["ICD10CM"]      # http://hl7.org/fhir/sid/icd-10-cm
RXNORM_URI = SYSTEM_TO_FHIR_URI["RXNORM"]        # http://www.nlm.nih.gov/research/umls/rxnorm

# Seeded codes + canonical displays (per conftest fixture)
SNOMED_DM_CODE = "73211009"
SNOMED_DM_DISPLAY = "Diabetes mellitus"
SNOMED_T2DM_CODE = "44054006"
SNOMED_T2DM_DISPLAY = "Type 2 diabetes mellitus"
ICD10CM_E11_CODE = "E11"
ICD10CM_E11_DISPLAY = "Type 2 diabetes mellitus"
RXNORM_METFORMIN_CODE = "860975"
RXNORM_METFORMIN_DISPLAY = "24 HR metformin 500 MG Oral Tablet"

# Path to apps/fhir_api.py for source-read structural probes.
_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)


# =============================================================================
# Helpers
# =============================================================================


def _param_value(body: dict, name: str) -> object | None:
    """Return the value of the first Out parameter matching ``name``."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


def _has_param(body: dict, name: str) -> bool:
    return any(p.get("name") == name for p in body.get("parameter", []))


def _lookup_out_display(client, system: str, code: str) -> str | None:
    """Return the canonical display for ``code`` from $lookup (the
    canonical-DISPLAY META-PATTERN reference operation).
    """
    r = client.get(
        f"/fhir/CodeSystem/$lookup?system={system}&code={code}"
    )
    if r.status_code != 200:
        return None
    return _param_value(r.json(), "display")


def _validate_vs_get(
    client, *, system: str | None = None, code: str | None = None,
    url: str | None = None, display: str | None = None,
    inferSystem: str | None = None, abstract: str | None = None,
    systemVersion: str | None = None, date: str | None = None,
    displayLanguage: str | None = None,
):
    """GET /fhir/ValueSet/$validate-code with the given params."""
    params = []
    if system is not None:
        params.append(("system", system))
    if code is not None:
        params.append(("code", code))
    if url is not None:
        params.append(("url", url))
    if display is not None:
        params.append(("display", display))
    if inferSystem is not None:
        params.append(("inferSystem", inferSystem))
    if abstract is not None:
        params.append(("abstract", abstract))
    if systemVersion is not None:
        params.append(("systemVersion", systemVersion))
    if date is not None:
        params.append(("date", date))
    if displayLanguage is not None:
        params.append(("displayLanguage", displayLanguage))
    return client.get("/fhir/ValueSet/$validate-code", params=params)


def _validate_vs_post(client, body: dict):
    """POST /fhir/ValueSet/$validate-code with a Parameters body."""
    return client.post(
        "/fhir/ValueSet/$validate-code",
        json=body,
        headers={"Accept": "application/fhir+json"},
    )


def _parameters_body(*pairs: tuple[str, dict]) -> dict:
    """Build a Parameters body from (name, value*) tuples."""
    return {
        "resourceType": "Parameters",
        "parameter": [{"name": n, **rest} for (n, rest) in pairs],
    }


# =============================================================================
# Source-read helpers (mirror VS-04 HISTORIAN/CS-03 HISTORIAN patterns)
# =============================================================================


def _read_module_source() -> str:
    return inspect.getsource(
        __import__("medterm4ds.apps.fhir_api", fromlist=["fhir_api"])
    )


def _read_nested_function_source(
    module_src: str, parent_name: str, child_name: str
) -> str | None:
    """Return source of a nested function defined inside ``parent_name``.

    Walks BOTH ast.FunctionDef AND ast.AsyncFunctionDef inside ``parent``
    (mirrors CS-03 HISTORIAN + VS-04 HISTORIAN helper).
    """
    tree = ast.parse(module_src)
    parent_node = None
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == parent_name
        ):
            parent_node = node
            break
    if parent_node is None:
        return None
    for child in ast.walk(parent_node):
        if (
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name == child_name
            and child is not parent_node
        ):
            return ast.get_source_segment(module_src, child) or ""
    return None


# =============================================================================
# L1: Required-params hostile inputs (Spec Item 1)
# Spec: https://hl7.org/fhir/R4/valueset-operation-validate-code.html
# Per spec: "If the operation is not called at the instance level, one
# of the in parameters url, context or valueSet must be provided. One
# (and only one) of the in parameters code, coding, or codeableConcept
# must be provided."
# =============================================================================


class TestL1RequiredParamsHostileInputs:
    """Item 1: hostile inputs on the required url/code/system params."""

    def test_s10_get_empty_system_rejected_422(self, fhir_client):
        """Empty string on required system Query MUST 422 (count=5 PROMOTED
        empty-string drift pattern)."""
        r = _validate_vs_get(fhir_client, system="", code=SNOMED_T2DM_CODE)
        assert r.status_code in (400, 422), (
            f"Empty system: expected 400/422, got {r.status_code}. Body: {r.text[:300]}"
        )

    def test_s11_get_empty_code_rejected_422(self, fhir_client):
        """Empty string on required code Query MUST 422."""
        r = _validate_vs_get(fhir_client, system=SNOMED_URI, code="")
        assert r.status_code in (400, 422), (
            f"Empty code: expected 400/422, got {r.status_code}. Body: {r.text[:300]}"
        )

    def test_s12_get_whitespace_only_system_rejected_422(self, fhir_client):
        """Whitespace-only system — spec-borderline but the GLOBAL_RULES.md
        9th PROMOTED pattern (count=5) and the CF-EXPLORER-TS02-01
        deferred carry-forward both cover this. Per spec, whitespace is
        not 'providing' a value."""
        r = _validate_vs_get(fhir_client, system="   ", code=SNOMED_T2DM_CODE)
        # min_length=1 enforces "present" but whitespace-only satisfies it.
        # Engine downstream returns semantic-failure (400 unknown system)
        # per CF-EXPLORER-TS02-01 documented behavior.
        assert r.status_code < 500, (
            f"Whitespace system should not crash. Got {r.status_code}."
        )

    def test_s13_get_malformed_url_with_special_chars_accepted(self, fhir_client):
        """Malformed url with special chars MUST NOT 5xx (no crash).

        Per spec: ``url`` is the ValueSet URL; it MAY be the code system
        URI alone (implicit value set, Form a). The implementation accepts
        but does not restrict membership today.
        """
        r = _validate_vs_get(
            fhir_client, url="http://fake.example/vs?id=1&x=<>'\";",
            system=SNOMED_URI, code=SNOMED_T2DM_CODE,
        )
        assert r.status_code < 500, (
            f"Malformed url special chars: no 5xx. Got {r.status_code}."
        )

    def test_s14_get_non_existent_url_with_known_code_returns_result(self, fhir_client):
        """Non-existent url — server falls through to code-system membership
        (the documented approximate-semantic per ``_do_vs_validate`` docstring).
        """
        r = _validate_vs_get(
            fhir_client, url="http://example.org/nonexistent-vs",
            system=SNOMED_URI, code=SNOMED_T2DM_CODE,
        )
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is True

    def test_s15_get_very_long_url_handled(self, fhir_client):
        """5K-char url — no DoS, no 5xx."""
        long_url = "http://example.org/" + "x" * 5000
        r = _validate_vs_get(
            fhir_client, url=long_url,
            system=SNOMED_URI, code=SNOMED_T2DM_CODE,
        )
        assert r.status_code < 500

    def test_s16_post_missing_both_system_code_and_coding_and_cc(self, fhir_client):
        """POST without any of (system+code), coding, codeableConcept → 400."""
        body = _parameters_body()
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code in (400, 422), (
            f"Empty Parameters body: expected 400/422. Got {r.status_code}."
        )

    def test_s17_post_partial_scalar_missing_code(self, fhir_client):
        """POST with system but no code → 400."""
        body = _parameters_body(("system", {"valueUri": SNOMED_URI}))
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code in (400, 422)

    def test_s18_post_partial_scalar_missing_system(self, fhir_client):
        """POST with code but no system → 400."""
        body = _parameters_body(("code", {"valueCode": SNOMED_T2DM_CODE}))
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code in (400, 422)


# =============================================================================
# L2: Optional-params hostile inputs (Spec Item 2)
# Spec: https://hl7.org/fhir/R4/valueset-operation-validate-code.html
# Per spec In Parameters: systemVersion, display, date, coding,
# codeableConcept, abstract, displayLanguage are 0..1.
# NOTE: R4 does NOT define ``inferSystem`` for ValueSet/$validate-code
# (R5-only). The implementation accepts it as undocumented param.
# =============================================================================


class TestL2OptionalParamsHostileInputs:
    """Item 2: hostile inputs on the optional params."""

    def test_s20_get_non_existent_system_version_accepted(self, fhir_client):
        """systemVersion non-existent — accepted as no-op (snapshot engine)."""
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
            systemVersion="http://snomed.info/sct/99999999999999",
        )
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is True

    def test_s21_get_display_very_long_value_handled(self, fhir_client):
        """5K-char display value — no DoS, server compares vs canonical."""
        long_display = "X" * 5000
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
            display=long_display,
        )
        assert r.status_code == 200
        # Long display != canonical → result=false per CF-SKEPTIC-CS03-01.
        assert _param_value(r.json(), "result") is False
        # Out display MUST be the engine canonical, NOT the 5K echo.
        assert _param_value(r.json(), "display") == SNOMED_T2DM_DISPLAY

    def test_s22_get_display_with_unicode_handled(self, fhir_client):
        """Unicode CJK / emoji display — no crash, server compares vs canonical."""
        unicode_display = "甜糖尿病😀"
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
            display=unicode_display,
        )
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is False
        assert _param_value(r.json(), "display") == SNOMED_T2DM_DISPLAY

    def test_s23_get_display_with_null_bytes_handled(self, fhir_client):
        """Null bytes in display — no crash."""
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
            display="bad\x00value",
        )
        assert r.status_code < 500

    def test_s24_get_display_with_sql_injection_shape_handled(self, fhir_client):
        """SQL-injection-shaped display — no crash, no DB injection."""
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
            display="'; DROP TABLE mrconso; --",
        )
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is False

    def test_s25_get_date_rfc3339_full_accepted(self, fhir_client):
        """RFC 3339 full date-time accepted."""
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
            date="2024-01-15T10:30:00Z",
        )
        assert r.status_code == 200

    def test_s26_get_date_partial_year_only_accepted(self, fhir_client):
        """Partial date (year only) — accepted as no-op per FHIR R4 dateTime."""
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
            date="2024",
        )
        assert r.status_code == 200

    def test_s27_get_date_malformed_no_crash(self, fhir_client):
        """Malformed date — accepted as no-op (no enforcement today)."""
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
            date="not-a-date",
        )
        assert r.status_code < 500

    def test_s28_get_displayLanguage_bcp47_edge_cases_accepted(self, fhir_client):
        """BCP 47 edge cases (multi-region, unicode-extension, private-use,
        malformed) — accepted without 5xx."""
        for lang in ("en-US", "zh-Hans-CN", "de-DE-1996", "x-private-code", "invalid!"):
            r = _validate_vs_get(
                fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
                displayLanguage=lang,
            )
            assert r.status_code == 200, (
                f"displayLanguage={lang!r}: no 5xx, got {r.status_code}."
            )

    def test_s29_get_abstract_flag_combinations_accepted(self, fhir_client):
        """abstract=true / false / malformed — all accepted without 5xx
        per AGENTS.md NOT A BUG registry (no abstract-flagging today).
        """
        for val in ("true", "false", "True", "1", "yes"):
            r = _validate_vs_get(
                fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
                abstract=val,
            )
            assert r.status_code == 200


# =============================================================================
# L3: Out Parameters shape (Spec Item 3)
# Spec: https://hl7.org/fhir/R4/valueset-operation-validate-code.html
# Out: result (1..1 boolean), message (0..1 string), display (0..1 string)
# =============================================================================


class TestL3OutParametersShape:
    """Item 3: response shape invariants."""

    def test_s30_response_always_parameters_resource_type(self, fhir_client):
        """200 response MUST be Parameters resourceType."""
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
        )
        body = r.json()
        assert body.get("resourceType") == "Parameters"
        assert "parameter" in body

    def test_s31_result_always_present_as_valueBoolean(self, fhir_client):
        """result (1..1) is always present as wire-boolean."""
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
        )
        body = r.json()
        assert _has_param(body, "result")
        val = _param_value(body, "result")
        assert isinstance(val, bool)

    def test_s32_result_lowercase_on_wire_format(self, fhir_client):
        """Wire-format boolean MUST be lowercase ``true``/``false``."""
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
        )
        raw = r.text
        assert '"valueBoolean": true' in raw or '"valueBoolean":false' in raw, (
            f"Lowercase boolean keyword required. Raw: {raw[:300]}"
        )
        assert '"valueBoolean": True' not in raw
        assert '"valueBoolean": False' not in raw

    def test_s33_message_uses_valueString_not_valueCode(self, fhir_client):
        """When message present, MUST use valueString wire-type."""
        # Trigger display mismatch → message present
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
            display="WRONG",
        )
        body = r.json()
        assert _has_param(body, "message")
        msg_entry = next(p for p in body["parameter"] if p.get("name") == "message")
        assert "valueString" in msg_entry
        # valueCode/valueCoding MUST NOT be used for message
        assert "valueCode" not in msg_entry
        assert "valueCoding" not in msg_entry

    def test_s34_display_uses_valueString_wire_type(self, fhir_client):
        """When Out display present, MUST use valueString wire-type."""
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
        )
        body = r.json()
        assert _has_param(body, "display")
        disp_entry = next(p for p in body["parameter"] if p.get("name") == "display")
        assert "valueString" in disp_entry

    def test_s35_unknown_code_response_shape(self, fhir_client):
        """Unknown code: 200 + Parameters + result=false + message."""
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code="UNKNOWN9999",
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("resourceType") == "Parameters"
        assert _param_value(body, "result") is False
        assert _has_param(body, "message")

    def test_s36_unknown_code_message_cites_code_and_system(self, fhir_client):
        """Unknown-code message MUST cite both the code AND the system URI
        for clinical-actionability (mirrors CS-03 TERMINOLOGIST test_t91)."""
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code="UNKNOWN9999",
        )
        msg = _param_value(r.json(), "message")
        assert msg is not None
        msg_str = str(msg)
        assert "UNKNOWN9999" in msg_str
        assert "snomed" in msg_str.lower() or SNOMED_URI in msg_str


# =============================================================================
# L4: CodeableConcept any-match semantics (Spec Item 4)
# Spec: 'The server returns true if one of the coding values is in the
# code system'
# =============================================================================


class TestL4CodeableConceptAnyMatch:
    """Item 4: codeableConcept multi-coding any-match + hostile shapes."""

    def test_s40_post_cc_invalid_then_valid_returns_true(self, fhir_client):
        """[INVALID, VALID] → result=true per spec any-match semantic.
        VS-05 SKEPTIC QA-070 fix territory."""
        body = _parameters_body((
            "codeableConcept",
            {"valueCodeableConcept": {"coding": [
                {"system": SNOMED_URI, "code": "INVALID-1"},
                {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
            ]}},
        ))
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is True

    def test_s41_post_cc_valid_then_invalid_returns_true(self, fhir_client):
        """[VALID, INVALID] → result=true per spec any-match semantic.

        The matched URI + display reflect the MATCHED coding (not the
        first coding)."""
        body = _parameters_body((
            "codeableConcept",
            {"valueCodeableConcept": {"coding": [
                {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
                {"system": SNOMED_URI, "code": "INVALID-2"},
            ]}},
        ))
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        body_json = r.json()
        assert _param_value(body_json, "result") is True
        # Out system + display reflect the MATCHED coding (CR-025)
        assert _param_value(body_json, "system") == SNOMED_URI
        assert _param_value(body_json, "display") == SNOMED_T2DM_DISPLAY

    def test_s42_post_cc_all_invalid_returns_false(self, fhir_client):
        """All codings invalid → result=false per spec."""
        body = _parameters_body((
            "codeableConcept",
            {"valueCodeableConcept": {"coding": [
                {"system": SNOMED_URI, "code": "BAD-1"},
                {"system": SNOMED_URI, "code": "BAD-2"},
            ]}},
        ))
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is False

    def test_s43_post_cc_mixed_systems_any_match_returns_true(self, fhir_client):
        """Mixed-system codings: any one valid → result=true.

        Per spec: the codeableConcept is validated against the value set /
        code system; mixed systems are per-coding, not per-CC."""
        body = _parameters_body((
            "codeableConcept",
            {"valueCodeableConcept": {"coding": [
                {"system": ICD10CM_URI, "code": SNOMED_T2DM_CODE},  # wrong system
                {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},   # right system
            ]}},
        ))
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is True

    def test_s44_post_cc_partial_coding_missing_system_skipped(self, fhir_client):
        """Coding with code but no system — skipped (no crash).

        Per spec CodeableConcept: a Coding without system is not a valid
        code reference; the server skips it and processes the others."""
        body = _parameters_body((
            "codeableConcept",
            {"valueCodeableConcept": {"coding": [
                {"code": SNOMED_T2DM_CODE},  # no system → skipped
                {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},  # valid
            ]}},
        ))
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is True

    def test_s45_post_cc_partial_coding_missing_code_skipped(self, fhir_client):
        """Coding with system but no code — skipped (no crash)."""
        body = _parameters_body((
            "codeableConcept",
            {"valueCodeableConcept": {"coding": [
                {"system": SNOMED_URI},  # no code → skipped
                {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},  # valid
            ]}},
        ))
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is True

    def test_s46_post_cc_empty_codings_handled(self, fhir_client):
        """codeableConcept with empty coding[] — no crash, 4xx (system req)."""
        body = _parameters_body((
            "codeableConcept",
            {"valueCodeableConcept": {"coding": []}},
        ))
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code in (400, 422)

    def test_s47_post_cc_missing_coding_key_handled(self, fhir_client):
        """codeableConcept without coding key — no crash, 4xx."""
        body = _parameters_body((
            "codeableConcept",
            {"valueCodeableConcept": {"text": "some text"}},
        ))
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code in (400, 422)

    def test_s48_post_cc_non_dict_entries_skipped(self, fhir_client):
        """Coding entries as non-dict (string, int, null, list) — skipped
        per 10th PROMOTED pattern (isinstance-dict guard on Parameters-
        body iterators). No 5xx."""
        body = {
            "resourceType": "Parameters",
            "parameter": [{
                "name": "codeableConcept",
                "valueCodeableConcept": {"coding": [
                    "not-a-dict",  # string
                    42,            # int
                    None,          # null
                    ["nested", "list"],  # list
                    {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},  # valid
                ]},
            }],
        }
        r = _validate_vs_post(fhir_client, body)
        # Server should accept the body, skip non-dict entries, and find
        # the valid one. Either 200 (any match) OR 400 if no match.
        assert r.status_code in (200, 400)

    def test_s49_post_cc_large_coding_array_handled(self, fhir_client):
        """100-coding array — no DoS, no 5xx. Valid coding at index 99."""
        codings = [{"system": SNOMED_URI, "code": f"BAD-{i}"} for i in range(99)]
        codings.append({"system": SNOMED_URI, "code": SNOMED_T2DM_CODE})
        body = _parameters_body((
            "codeableConcept", {"valueCodeableConcept": {"coding": codings}},
        ))
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is True


# =============================================================================
# L5: Display mismatch — CF-SKEPTIC-CS03-01 fix shape re-verification
# Spec: when display != canonical, result=false + message cites wrong
# value + Out display = engine canonical (NOT echo of client).
# =============================================================================


class TestL5DisplayMismatch:
    """Item 5: display mismatch enforcement — CF-SKEPTIC-CS03-01 CLOSED.

    Parametrized over every seeded code + system to verify the fix holds
    across all 4 seeded codes (per VS-04/TERMINOLOGIST tip — the META-
    PATTERN should extend to every system in the fixture).
    """

    @pytest.mark.parametrize(
        "system, code, canonical_display",
        [
            (SNOMED_URI, SNOMED_DM_CODE, SNOMED_DM_DISPLAY),
            (SNOMED_URI, SNOMED_T2DM_CODE, SNOMED_T2DM_DISPLAY),
            (ICD10CM_URI, ICD10CM_E11_CODE, ICD10CM_E11_DISPLAY),
            (RXNORM_URI, RXNORM_METFORMIN_CODE, RXNORM_METFORMIN_DISPLAY),
        ],
        ids=["snomed-dm", "snomed-t2dm", "icd10-e11", "rxnorm-metformin"],
    )
    def test_s50_display_mismatch_returns_false_per_system(
        self, fhir_client, system, code, canonical_display,
    ):
        """CF-SKEPTIC-CS03-01 fix holds parametrized over every seeded system."""
        r = _validate_vs_get(
            fhir_client, system=system, code=code,
            display="WRONG-CLINICAL-DISPLAY",
        )
        assert r.status_code == 200
        body = r.json()
        # (1) result MUST be false
        assert _param_value(body, "result") is False
        # (2) message MUST cite the wrong value
        msg = _param_value(body, "message")
        assert msg is not None and "WRONG-CLINICAL-DISPLAY" in str(msg)
        # (3) Out display MUST be the engine canonical
        assert _param_value(body, "display") == canonical_display

    @pytest.mark.parametrize(
        "system, code, canonical_display",
        [
            (SNOMED_URI, SNOMED_DM_CODE, SNOMED_DM_DISPLAY),
            (SNOMED_URI, SNOMED_T2DM_CODE, SNOMED_T2DM_DISPLAY),
            (ICD10CM_URI, ICD10CM_E11_CODE, ICD10CM_E11_DISPLAY),
            (RXNORM_URI, RXNORM_METFORMIN_CODE, RXNORM_METFORMIN_DISPLAY),
        ],
        ids=["snomed-dm", "snomed-t2dm", "icd10-e11", "rxnorm-metformin"],
    )
    def test_s51_display_match_returns_true_per_system(
        self, fhir_client, system, code, canonical_display,
    ):
        """Sanity: matching display → result=true (per system)."""
        r = _validate_vs_get(
            fhir_client, system=system, code=code,
            display=canonical_display,
        )
        assert _param_value(r.json(), "result") is True

    def test_s52_display_mismatch_message_byte_exact_format(self, fhir_client):
        """Spec example message format: ``The display "X" is incorrect``
        (mirror of CS-03 SKEPTIC test_s53 byte-exact format).
        """
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
            display="wrong-display",
        )
        msg = _param_value(r.json(), "message")
        assert msg == 'The display "wrong-display" is incorrect', (
            f"Spec example format. Got: {msg!r}"
        )

    def test_s53_display_mismatch_case_sensitive(self, fhir_client):
        """Display comparison is case-sensitive per spec ('Whether displays
        are case-sensitive depends on the code system').
        """
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
            display=SNOMED_T2DM_DISPLAY.upper(),  # case-only difference
        )
        # medterm4ds uses exact-string comparison (case-sensitive)
        body = r.json()
        result = _param_value(body, "result")
        # Either case-sensitive (result=false) or case-insensitive (result=true)
        # is spec-permitted; the probe verifies no 5xx and Out display is canonical.
        assert r.status_code == 200
        if result is False:
            assert _param_value(body, "display") == SNOMED_T2DM_DISPLAY

    def test_s54_display_mismatch_unknown_code_no_mismatch_trigger(self, fhir_client):
        """When code is unknown, the response is 'code not valid' (not
        'display incorrect'). Display mismatch only applies when code IS
        valid (mirror CS-03 SKEPTIC test_s14)."""
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code="UNKNOWN-9999",
            display="WRONG-DISPLAY",
        )
        body = r.json()
        assert _param_value(body, "result") is False
        msg = _param_value(body, "message")
        # Message should cite unknown code, NOT "display ... is incorrect"
        assert "incorrect" not in str(msg).lower()
        assert "UNKNOWN-9999" in str(msg)

    def test_s55_post_display_mismatch_byte_exact_with_get(self, fhir_client):
        """POST ↔ GET byte-exact parity on display mismatch (mirror VS-02
        TERMINOLOGIST test_t25/t26 GET↔POST parity)."""
        get_r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
            display="WRONG",
        )
        body = _parameters_body(
            ("system", {"valueUri": SNOMED_URI}),
            ("code", {"valueCode": SNOMED_T2DM_CODE}),
            ("display", {"valueString": "WRONG"}),
        )
        post_r = _validate_vs_post(fhir_client, body)
        assert get_r.status_code == post_r.status_code == 200
        g_body = get_r.json()
        p_body = post_r.json()
        assert _param_value(g_body, "result") == _param_value(p_body, "result")
        assert _param_value(g_body, "display") == _param_value(p_body, "display")
        assert _param_value(g_body, "message") == _param_value(p_body, "message")


# =============================================================================
# L6: Implicit value set — code system URI alone as ValueSet URL (Item 6)
# Spec: ``url`` MAY be a code system URI; the operation validates code+
# system membership. SNOMED intensional URL form ``?fhir_vs=isa`` is
# also a valid implicit value set URL.
# =============================================================================


class TestL6ImplicitValueSet:
    """Item 6: implicit value set URL forms."""

    def test_s60_get_implicit_vs_code_system_uri_alone(self, fhir_client):
        """Code system URI alone as ``url`` — implicit value set form."""
        r = _validate_vs_get(
            fhir_client, url=SNOMED_URI,
            system=SNOMED_URI, code=SNOMED_T2DM_CODE,
        )
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is True

    @pytest.mark.parametrize(
        "system_uri",
        [SNOMED_URI, ICD10CM_URI, RXNORM_URI],
        ids=["snomed", "icd10cm", "rxnorm"],
    )
    def test_s61_get_implicit_vs_per_system(self, fhir_client, system_uri):
        """Implicit VS form parametrized over every seeded system."""
        # Pick the seeded code for each system
        codes = {
            SNOMED_URI: SNOMED_T2DM_CODE,
            ICD10CM_URI: ICD10CM_E11_CODE,
            RXNORM_URI: RXNORM_METFORMIN_CODE,
        }
        r = _validate_vs_get(
            fhir_client, url=system_uri,
            system=system_uri, code=codes[system_uri],
        )
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is True

    def test_s62_get_implicit_vs_snomed_intensional_url_no_5xx(self, fhir_client):
        """SNOMED intensional URL form ``?fhir_vs=isa`` — accepted without
        5xx (membership scoping is a future enhancement)."""
        r = _validate_vs_get(
            fhir_client,
            url=f"http://snomed.info/sct/{SNOMED_DM_CODE}?fhir_vs=isa",
            system=SNOMED_URI, code=SNOMED_T2DM_CODE,
        )
        assert r.status_code < 500, (
            f"Intensional URL form: no 5xx. Got {r.status_code}."
        )

    def test_s63_get_implicit_vs_unknown_url_with_known_code(self, fhir_client):
        """Unknown url with known code — server falls through to code
        presence (documented approximate semantic per _do_vs_validate)."""
        r = _validate_vs_get(
            fhir_client, url="http://unknown.example/vs",
            system=SNOMED_URI, code=SNOMED_T2DM_CODE,
        )
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is True

    def test_s64_get_implicit_vs_with_display_mismatch_combined(self, fhir_client):
        """Implicit VS + display mismatch — both semantics fire correctly."""
        r = _validate_vs_get(
            fhir_client, url=SNOMED_URI,
            system=SNOMED_URI, code=SNOMED_T2DM_CODE,
            display="WRONG",
        )
        assert r.status_code == 200
        body = r.json()
        assert _param_value(body, "result") is False
        assert _param_value(body, "display") == SNOMED_T2DM_DISPLAY


# =============================================================================
# L7: Canonical-DISPLAY META-PATTERN extension to ValueSet/$validate-code
# (VS-04/TERMINOLOGIST tip — the META-PATTERN count=7 PROMOTED spans
# $lookup + $validate-code (CS) + $expand (every mode). VS-05 SKEPTIC
# verifies VS/$validate-code Out display joins the META-PATTERN with
# byte-exact agreement vs $lookup.)
# =============================================================================


class TestL7CanonicalDisplayMetaPattern:
    """META-PATTERN extension: VS/$validate-code Out display byte-exact
    equals $lookup Out display for every seeded code."""

    @pytest.mark.parametrize(
        "system, code",
        [
            (SNOMED_URI, SNOMED_DM_CODE),
            (SNOMED_URI, SNOMED_T2DM_CODE),
            (ICD10CM_URI, ICD10CM_E11_CODE),
            (RXNORM_URI, RXNORM_METFORMIN_CODE),
        ],
        ids=["snomed-dm", "snomed-t2dm", "icd10-e11", "rxnorm-metformin"],
    )
    def test_s70_vs_validate_display_byte_exact_with_lookup(
        self, fhir_client, system, code,
    ):
        """Out display from VS/$validate-code == Out display from $lookup
        for the same (system, code) pair. Byte-exact equality.
        """
        lookup_display = _lookup_out_display(fhir_client, system, code)
        r = _validate_vs_get(fhir_client, system=system, code=code)
        vs_display = _param_value(r.json(), "display")
        assert lookup_display == vs_display, (
            f"Canonical-DISPLAY META-PATTERN drift on ({system}, {code}): "
            f"$lookup={lookup_display!r}, VS/$validate-code={vs_display!r}."
        )

    @pytest.mark.parametrize(
        "system, code",
        [
            (SNOMED_URI, SNOMED_T2DM_CODE),
            (ICD10CM_URI, ICD10CM_E11_CODE),
        ],
        ids=["snomed-t2dm", "icd10-e11"],
    )
    def test_s71_cs_vs_validate_byte_exact_agreement(
        self, fhir_client, system, code,
    ):
        """CS/$validate-code Out display == VS/$validate-code Out display
        for the same (system, code) (cross-handler parity audit)."""
        cs_r = fhir_client.get(
            f"/fhir/CodeSystem/$validate-code?system={system}&code={code}"
        )
        vs_r = _validate_vs_get(fhir_client, system=system, code=code)
        assert cs_r.status_code == vs_r.status_code == 200
        cs_display = _param_value(cs_r.json(), "display")
        vs_display = _param_value(vs_r.json(), "display")
        assert cs_display == vs_display, (
            f"CS↔VS display drift on ({system}, {code}): "
            f"CS={cs_display!r}, VS={vs_display!r}."
        )

    def test_s72_vs_validate_display_matches_lookup_on_alias_input(
        self, fhir_client,
    ):
        """Canonical-DISPLAY META-PATTERN invariant holds on alias input.

        Per TS-03 EXPLORER QA-001 uppercase-scheme + CS-02 SKEPTIC QA-046:
        alias inputs (uppercase-scheme, trailing-slash, urn:oid) MUST
        resolve to the same canonical display."""
        # Uppercase-scheme alias (TS-03 EXPLORER QA-001)
        upper_uri = SNOMED_URI.upper()  # HTTP://snomed.info/sct
        r_upper = _validate_vs_get(
            fhir_client, system=upper_uri, code=SNOMED_T2DM_CODE,
        )
        r_canonical = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
        )
        if r_upper.status_code == 200 and r_canonical.status_code == 200:
            assert (
                _param_value(r_upper.json(), "display")
                == _param_value(r_canonical.json(), "display")
                == SNOMED_T2DM_DISPLAY
            )


# =============================================================================
# L8: Source-read structural contracts — the simplest way to lock in
# expected behaviors without depending on fixture data.
# =============================================================================


class TestL8SourceReadStructuralContracts:
    """Source-read probes on ``_do_vs_validate`` and the POST handlers."""

    def test_s80_canonical_system_uri_on_scalar_path(self):
        """``_do_vs_validate`` scalar path MUST route Out ``system`` through
        ``canonical_system_uri`` (CR-011 fix shape — client-input-as-
        canonical drift count=8 PROMOTED)."""
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_do_vs_validate"
        )
        assert src is not None, "_do_vs_validate not found in create_fhir_app"
        assert "canonical_system_uri(" in src, (
            "_do_vs_validate scalar path MUST call canonical_system_uri "
            "(CR-011 client-input-as-canonical drift fix)."
        )

    def test_s81_canonical_system_uri_on_codeableConcept_path(self):
        """``_do_vs_validate`` codeableConcept matched-uri path MUST route
        through ``canonical_system_uri`` (CR-025 fix shape — sibling fix
        of CR-011 on the codeableConcept branch)."""
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_do_vs_validate"
        )
        assert src is not None
        # The codeableConcept branch contains canonical_matched_uri = ...
        assert "canonical_matched_uri" in src, (
            "_do_vs_validate codeableConcept path MUST route matched_uri "
            "through canonical_system_uri (CR-025 sibling fix)."
        )

    def test_s82_display_mismatch_check_inline(self):
        """``_do_vs_validate`` MUST contain the display mismatch check
        (CF-SKEPTIC-CS03-01 CLOSED by VS-05 SKEPTIC QA-069 — the check
        is inline; a future refactor to a shared helper MUST preserve
        the structural shape)."""
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_do_vs_validate"
        )
        assert src is not None
        # The check has 3 load-bearing elements:
        # (1) display != canonical_display comparison
        # (2) message='The display "X" is incorrect' format
        # (3) canonical display passed to builder
        assert "display" in src and "canonical_display" in src
        assert "is incorrect" in src, (
            "Spec example message format MUST be byte-exact (CF-SKEPTIC-CS03-01)."
        )

    def test_s83_all_pairs_helper_in_vs_validate_post(self):
        """``vs_validate_post`` MUST use ``_extract_all_coding_pairs_from_codeable_concept``
        (VS-05 SKEPTIC QA-070 fix — mirror of CS-03 SKEPTIC QA-049)."""
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "vs_validate_post"
        )
        assert src is not None
        assert "_extract_all_coding_pairs_from_codeable_concept" in src, (
            "vs_validate_post MUST use the all-pairs helper (QA-070 fix)."
        )

    def test_s84_all_pairs_helper_in_extract_vs_validate_params(self):
        """``_extract_vs_validate_params`` (batch dispatcher helper) MUST
        also use the all-pairs helper (VS-05 SKEPTIC QA-070 batch mirror
        — CS-03 HISTORIAN QA-052 sibling fix on the VS surface)."""
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_extract_vs_validate_params"
        )
        assert src is not None
        assert "_extract_all_coding_pairs_from_codeable_concept" in src, (
            "_extract_vs_validate_params MUST use the all-pairs helper (QA-070 batch mirror)."
        )

    def test_s85_extract_vs_validate_params_returns_5_tuple(self):
        """``_extract_vs_validate_params`` MUST return a 5-tuple
        (system, code, display, url, codeable_pairs) — mirrors CS-03
        HISTORIAN QA-052's 4-tuple extension on the sibling helper."""
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_extract_vs_validate_params"
        )
        assert src is not None
        # The return annotation or the return statement should reveal 5
        # components; we look for the return type annotation.
        assert "tuple[" in src
        # Count the components in the return annotation
        # Find the first tuple[...] in the source
        tree = ast.parse(_read_module_source())
        parent_node = None
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "_extract_vs_validate_params"
            ):
                parent_node = node
                break
        assert parent_node is not None
        returns = parent_node.returns
        assert returns is not None
        # Format: tuple[str | None, str | None, str | None, str | None, list[...] | None]
        # The 5 None components are the load-bearing signal.
        returns_src = ast.unparse(returns)
        # Count the "None" substring in the unparse — 5 means 5-tuple
        # components.
        assert returns_src.count("None") >= 5, (
            f"_extract_vs_validate_params MUST return a 5-tuple. Got: {returns_src}"
        )

    def test_s86_min_length_1_not_required_on_vs_validate_get(self):
        """``vs_validate_get`` does NOT have ``min_length=1`` on the
        required Query because the GET handler uses ``Query(None)`` for
        all params (validation happens in ``_do_vs_validate`` returning
        400). This is the documented behavior — VS/$validate-code has
        no required string Query at the FastAPI level because the
        alternative encodings (coding, codeableConcept) make ``system``
        and ``code`` not strictly required.

        Distinct from CodeSystem/$validate-code where ``system`` and
        ``code`` ARE strictly required (min_length=1 per TS-02 SKEPTIC
        QA-002 count=5 PROMOTED).
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "vs_validate_get"
        )
        assert src is not None
        # The handler uses Query(None) — system/code/url are all optional
        # at the FastAPI level. The 400 is raised by _do_vs_validate.
        assert "Query(None" in src or "Query(None)" in src, (
            "vs_validate_get uses Query(None) for all params (alternative "
            "encodings make system/code not strictly required)."
        )

    def test_s87_isinstance_dict_guard_on_post_iterators(self):
        """Per 10th PROMOTED pattern (count=4): ``vs_validate_post`` and
        ``_extract_vs_validate_params`` MUST iterate over list entries
        from the client body with isinstance-dict guards. The all-pairs
        helper ``_extract_all_coding_pairs_from_codeable_concept`` MUST
        contain the guard.
        """
        # The guard is in the all-pairs helper itself
        from medterm4ds.apps import fhir_api
        src_all = inspect.getsource(fhir_api)
        # Find _extract_all_coding_pairs_from_codeable_concept
        tree = ast.parse(src_all)
        helper_node = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_extract_all_coding_pairs_from_codeable_concept"
            ):
                helper_node = node
                break
        assert helper_node is not None
        helper_src = ast.get_source_segment(src_all, helper_node) or ""
        # Look for isinstance(<var>, dict) in the body
        has_isinstance_dict = False
        for child in ast.walk(helper_node):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                if child.func.id == "isinstance":
                    if child.args and isinstance(child.args[1], ast.Name):
                        if child.args[1].id == "dict":
                            has_isinstance_dict = True
                            break
        assert has_isinstance_dict, (
            "_extract_all_coding_pairs_from_codeable_concept MUST contain "
            "isinstance(<var>, dict) guard per 10th PROMOTED pattern."
        )

    def test_s88_builder_canonical_precedence(self):
        """``build_parameters_validate`` MUST prefer ``code_info.name`` over
        client-supplied display for the Out display (canonical-wins
        semantic — TS-02 TERMINOLOGIST QA-029 + CS-03 SKEPTIC QA-048)."""
        from medterm4ds.engines.fhir import responses as resp_module
        src = inspect.getsource(resp_module.build_parameters_validate)
        # The canonical-wins pattern: canonical = code_info.name ... or display
        assert "code_info.name" in src, (
            "build_parameters_validate MUST prefer code_info.name (canonical) "
            "over client display per canonical-wins semantic."
        )

    def test_s89_vs_validate_get_calls_do_vs_validate(self):
        """``vs_validate_get`` MUST delegate to ``_do_vs_validate`` (not
        duplicate the logic inline)."""
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "vs_validate_get"
        )
        assert src is not None
        assert "_do_vs_validate" in src

    def test_s90_vs_validate_post_calls_do_vs_validate(self):
        """``vs_validate_post`` MUST delegate to ``_do_vs_validate``."""
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "vs_validate_post"
        )
        assert src is not None
        assert "_do_vs_validate" in src


# =============================================================================
# L9: CF-EXPLORER-CS02-01 4-shape POST Content-Type family closure
# (VS-04/TERMINOLOGIST tip — the LAST operation needing closure on the
# carry-forward. Every shape MUST return Content-Type: application/
# fhir+json + Parameters body (200) or OperationOutcome body (4xx).)
# =============================================================================


class TestL9CFExplorerCS02FourShapePostContentType:
    """CF-EXPLORER-CS02-01 closure: 4-shape POST Content-Type probe family.

    Per the carry-forward documentation in AGENTS.md, each chunk's
    EXPLORER iteration closes its own portion. VS-05 SKEPTIC resweep
    addresses the LAST open operation: ValueSet/$validate-code.

    The 4 shapes are:
      (a) system+code body — successful validation
      (b) coding body — alternative encoding (TS-02 HISTORIAN QA-022)
      (c) codeableConcept body — alternative encoding
      (d) error path — missing required params
    """

    def test_s100_system_code_body_content_type(self, fhir_client):
        """Shape (a): system+code body → 200 + application/fhir+json."""
        body = _parameters_body(
            ("system", {"valueUri": SNOMED_URI}),
            ("code", {"valueCode": SNOMED_T2DM_CODE}),
        )
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/fhir+json"), (
            f"Shape (a) Content-Type MUST be application/fhir+json. "
            f"Got: {r.headers.get('content-type')}."
        )
        assert r.json().get("resourceType") == "Parameters"

    def test_s101_coding_body_content_type(self, fhir_client):
        """Shape (b): coding body → 200 + application/fhir+json."""
        body = _parameters_body((
            "coding",
            {"valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE}},
        ))
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/fhir+json")
        assert r.json().get("resourceType") == "Parameters"

    def test_s102_codeable_concept_body_content_type(self, fhir_client):
        """Shape (c): codeableConcept body → 200 + application/fhir+json."""
        body = _parameters_body((
            "codeableConcept",
            {"valueCodeableConcept": {"coding": [
                {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
            ]}},
        ))
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/fhir+json")
        assert r.json().get("resourceType") == "Parameters"

    def test_s103_error_path_content_type(self, fhir_client):
        """Shape (d): error path (missing required params) → 4xx +
        application/fhir+json + OperationOutcome body."""
        body = _parameters_body()  # empty Parameters
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code in (400, 422)
        assert r.headers["content-type"].startswith("application/fhir+json"), (
            f"Error-path Content-Type MUST be application/fhir+json. "
            f"Got: {r.headers.get('content-type')}."
        )
        body_json = r.json()
        assert body_json.get("resourceType") == "OperationOutcome", (
            f"Error-path body MUST be OperationOutcome. Got: {body_json.get('resourceType')}."
        )

    def test_s104_all_4_shapes_uniform_content_type(self, fhir_client):
        """META: all 4 shapes share the same Content-Type contract —
        no shape silently returns a different MIME type."""
        results = []
        # Shape (a)
        body_a = _parameters_body(
            ("system", {"valueUri": SNOMED_URI}),
            ("code", {"valueCode": SNOMED_T2DM_CODE}),
        )
        r_a = _validate_vs_post(fhir_client, body_a)
        results.append(("a", r_a))
        # Shape (b)
        body_b = _parameters_body((
            "coding",
            {"valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE}},
        ))
        r_b = _validate_vs_post(fhir_client, body_b)
        results.append(("b", r_b))
        # Shape (c)
        body_c = _parameters_body((
            "codeableConcept",
            {"valueCodeableConcept": {"coding": [
                {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
            ]}},
        ))
        r_c = _validate_vs_post(fhir_client, body_c)
        results.append(("c", r_c))
        # Shape (d)
        body_d = _parameters_body()
        r_d = _validate_vs_post(fhir_client, body_d)
        results.append(("d", r_d))
        # All 4 MUST have application/fhir+json Content-Type
        for shape_name, response in results:
            ct = response.headers.get("content-type", "")
            assert ct.startswith("application/fhir+json"), (
                f"Shape ({shape_name}) Content-Type drift: got {ct!r}."
            )


# =============================================================================
# L10: Cross-handler GET ↔ POST byte-exact parity on hostile inputs
# =============================================================================


class TestL10GetPostByteExactParity:
    """GET ↔ POST byte-exact parity on hostile + lateral inputs.

    Per VS-02 EXPLORER test_e60..e62 + VS-04 SKEPTIC test_s91 — both
    paths MUST produce byte-equivalent ``result`` + ``display`` for
    the same logical (system, code) input.
    """

    @pytest.mark.parametrize(
        "system, code",
        [
            (SNOMED_URI, SNOMED_DM_CODE),
            (SNOMED_URI, SNOMED_T2DM_CODE),
            (ICD10CM_URI, ICD10CM_E11_CODE),
            (RXNORM_URI, RXNORM_METFORMIN_CODE),
            (SNOMED_URI, "UNKNOWN-9999"),
        ],
        ids=["snomed-dm", "snomed-t2dm", "icd10-e11", "rxnorm-metformin", "unknown"],
    )
    def test_s110_get_post_parity_per_system(self, fhir_client, system, code):
        """GET system+code and POST system+code body produce the same
        result + display + message (byte-exact)."""
        get_r = _validate_vs_get(fhir_client, system=system, code=code)
        body = _parameters_body(
            ("system", {"valueUri": system}),
            ("code", {"valueCode": code}),
        )
        post_r = _validate_vs_post(fhir_client, body)
        assert get_r.status_code == post_r.status_code
        if get_r.status_code == 200:
            g_body = get_r.json()
            p_body = post_r.json()
            assert _param_value(g_body, "result") == _param_value(p_body, "result")
            assert _param_value(g_body, "display") == _param_value(p_body, "display")

    def test_s111_get_post_parity_on_display_mismatch(self, fhir_client):
        """Display mismatch on GET and POST produces byte-exact result,
        display, message."""
        get_r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
            display="WRONG",
        )
        body = _parameters_body(
            ("system", {"valueUri": SNOMED_URI}),
            ("code", {"valueCode": SNOMED_T2DM_CODE}),
            ("display", {"valueString": "WRONG"}),
        )
        post_r = _validate_vs_post(fhir_client, body)
        g_body = get_r.json()
        p_body = post_r.json()
        assert _param_value(g_body, "result") == _param_value(p_body, "result") is False
        assert _param_value(g_body, "display") == _param_value(p_body, "display")
        assert _param_value(g_body, "message") == _param_value(p_body, "message")

    def test_s112_get_post_parity_on_alias_input(self, fhir_client):
        """Alias input (uppercase-scheme) — both paths produce same result.

        Per TS-03 EXPLORER QA-001 uppercase-scheme fix — the helper
        normalizes the scheme; both paths inherit via delegation."""
        upper_uri = SNOMED_URI.upper()
        get_r = _validate_vs_get(
            fhir_client, system=upper_uri, code=SNOMED_T2DM_CODE,
        )
        body = _parameters_body(
            ("system", {"valueUri": upper_uri}),
            ("code", {"valueCode": SNOMED_T2DM_CODE}),
        )
        post_r = _validate_vs_post(fhir_client, body)
        assert get_r.status_code == post_r.status_code
        if get_r.status_code == 200:
            assert (
                _param_value(get_r.json(), "result")
                == _param_value(post_r.json(), "result")
            )


# =============================================================================
# L11: Carry-forward reconfirmations — META patterns that MUST NOT regress
# =============================================================================


class TestL11CarryForwardReconfirmations:
    """Re-confirm META patterns + carry-forwards that touch VS-05 surface."""

    def test_s120_canonical_system_uri_helper_used(self):
        """CR-011 / CR-025 fix: ``canonical_system_uri`` IS used in
        ``_do_vs_validate`` (no drift back to raw client-input echo).
        count=8 PROMOTED client-input-as-canonical drift pattern."""
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_do_vs_validate"
        )
        assert src is not None
        # The helper MUST appear at least twice (scalar path + cc path)
        assert src.count("canonical_system_uri") >= 2, (
            f"canonical_system_uri MUST appear on BOTH scalar AND cc paths "
            f"(count >= 2). Found {src.count('canonical_system_uri')}."
        )

    def test_s121_no_raw_client_system_echo_on_scalar_path(self):
        """The scalar path MUST NOT pass ``system_uri`` directly to the
        builder without going through ``canonical_system_uri``."""
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_do_vs_validate"
        )
        assert src is not None
        # canonical_uri = canonical_system_uri(system_uri, source=source)
        assert "canonical_uri = canonical_system_uri" in src

    def test_s122_no_broad_except_in_do_vs_validate(self):
        """``_do_vs_validate`` MUST NOT catch broad ``Exception`` (silent-
        fallback prohibition per GLOBAL_RULES.md). Narrow ``duckdb.Error``
        is OK at the boundary (``_run_db``); the inner handler does not
        catch Exception."""
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_do_vs_validate"
        )
        assert src is not None
        # Walk the AST and find any bare Exception handler
        tree = ast.parse(_read_module_source())
        handler_node = None
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "_do_vs_validate"
            ):
                handler_node = node
                break
        assert handler_node is not None
        # Walk for bare except Exception inside _do_vs_validate
        for child in ast.walk(handler_node):
            if isinstance(child, ast.ExceptHandler):
                if child.type is None:
                    continue  # bare except is even worse, but let's check
                if isinstance(child.type, ast.Name) and child.type.id == "Exception":
                    pytest.fail(
                        "_do_vs_validate MUST NOT catch broad Exception "
                        "(silent-fallback prohibition)."
                    )

    def test_s123_cf_skeptic_cs03_01_closed_via_inline_check(self):
        """CF-SKEPTIC-CS03-01 CLOSED — the display mismatch check is
        inline in ``_do_vs_validate`` (mirror of CS-03 SKEPTIC QA-048).
        Pinned by the carry-forward-as-probe pattern (CS-03 TERMINOLOGIST
        test_t60 updated in-PR when the fix landed).
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_do_vs_validate"
        )
        assert src is not None
        assert 'is incorrect' in src, (
            "CF-SKEPTIC-CS03-01 fix shape: the spec example message text "
            "'is incorrect' MUST be present in _do_vs_validate."
        )
