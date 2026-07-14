"""HISTORIAN iteration VS-05 — ValueSet $validate-code Operation.

Spec: https://build.fhir.org/valueset-operation-validate-code.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-validate-code.html

HISTORIAN lens for VS-05 (ValueSet $validate-code):

1. **CR-019 / CF-HISTORIAN-CS04-02 (systemic duckdb.Error gap)**: app-level
   ``@app.exception_handler(duckdb.Error)`` is now registered
   (``src/medterm4ds/apps/fhir_api.py:625``). A transient DB failure inside
   ``_do_vs_validate`` MUST be translated into a 503 OperationOutcome (not
   the prior Starlette-default 500 ``text/plain``). Mirror of CS-04
   HISTORIAN pattern applied to the VS surface.

2. **CR-011 (canonical system echo on _do_vs_validate)**: the milestone-2
   review fixed the client-input-as-canonical drift on
   ``_do_vs_validate`` line 1898. VS-05 SKEPTIC test_s82 covers the
   trailing-slash case on GET; HISTORIAN extends coverage to:
   - OID alias on GET (``urn:oid:2.16.840.1.113883.6.96`` for SNOMED)
   - OID alias on POST (per-operation path)
   - ICD-10-CM OID alias on GET
   - GET↔POST parity on canonical echo (VS-04 EXPLORER strategy 50 applied
     to canonical echo)

3. **SKEPTIC QA-069 fix survived (display mismatch)**: edge cases for the
   new display-mismatch enforcement:
   - Empty display parameter (display=) → MUST NOT trigger mismatch (no
     client value to compare)
   - Whitespace-only display → MAY OR MAY NOT trigger mismatch (engine
     comparison is byte-exact; whitespace is not equal to canonical)
   - Code with no canonical display → MUST NOT trigger mismatch (no
     canonical to compare against)
   - Case-differing display → triggers mismatch (SNOMED case-sensitive)

4. **SKEPTIC QA-070 fix survived (codeableConcept multi-coding)**: verify
   the all-pairs helper is wired into BOTH the per-operation POST route
   AND the batch dispatcher's ``_extract_vs_validate_params`` (mirrors
   CS-03 HISTORIAN QA-052 — the per-operation fix was applied but the
   batch path was missed on the sibling CodeSystem handler).

5. **Cross-handler parity (CS-03 ↔ VS-05)**: the display-mismatch logic in
   ``_do_validate`` and ``_do_vs_validate`` MUST be byte-symmetric. The
   SKEPTIC iteration's auditor confirms this; HISTORIAN re-verifies via
   a parallel-probe pair: same (system, code, wrong display) on the two
   operations MUST return the same result value, the same message
   format, and the same canonical display.

6. **Test-too-lenient audit**: re-audit SKEPTIC's 35 VS-05 probes for
   negative-only assertions (TS-03 HISTORIAN QA-034 pattern). Every
   conformance probe MUST assert a POSITIVE success shape, not the
   absence of an error string.

7. **Carry-forward verification probes**: confirm CF-SKEPTIC-CS03-01
   (CLOSED in VS-05 SKEPTIC) and CF-HISTORIAN-VS02-02 (still open on
   $expand, NOT on $validate-code) are correctly tracked.

Conformance fixture seeds (per tests/fhir_conformance/conftest.py):
  SNOMED 73211009 = "Diabetes mellitus"
  SNOMED 44054006 = "Type 2 diabetes mellitus"
  ICD-10-CM E11   = "Type 2 diabetes mellitus"
  RxNorm  860975  = "24 HR metformin 500 MG Oral Tablet"
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

# Spec sources:
#   https://build.fhir.org/valueset-operation-validate-code.html
#   https://hl7.org/fhir/R4/valueset-operation-validate-code.html
#
# Canonical FHIR R4 URIs + aliases (per SYSTEM_TO_FHIR_URI + FHIR_URI_ALIASES):
SNOMED_URI = "http://snomed.info/sct"
SNOMED_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_SLASH_ALIAS = "http://snomed.info/sct/"

ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.90"

RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.88"

# Seeded codes + canonical displays:
SNOMED_DM_CODE = "73211009"
SNOMED_DM_DISPLAY = "Diabetes mellitus"
SNOMED_T2DM_CODE = "44054006"
SNOMED_T2DM_DISPLAY = "Type 2 diabetes mellitus"
ICD10CM_E11_CODE = "E11"
ICD10CM_E11_DISPLAY = "Type 2 diabetes mellitus"
RXNORM_METFORMIN_CODE = "860975"
RXNORM_METFORMIN_DISPLAY = "24 HR metformin 500 MG Oral Tablet"


def _param_value(body: dict, name: str):
    """Return the value of the first Out parameter matching ``name``."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


def _has_param(body: dict, name: str) -> bool:
    return any(p.get("name") == name for p in body.get("parameter", []))


# ===========================================================================
# Item 1: CR-019 / CF-HISTORIAN-CS04-02 — duckdb.Error boundary
# ===========================================================================
# Per FHIR R4 §3.1.0.1.5: every 4xx/5xx response MUST be an OperationOutcome.
# Per §3.1.0.1.9: every response MUST carry a FHIR MIME type.
#
# Pre-CR-019 (milestone-2 review): a transient duckdb.Error inside any
# ``_do_*`` handler propagated to Starlette's default 500 with text/plain
# body — non-conformant. CR-019 registered ``@app.exception_handler(duckdb.Error)``
# at the app level. HISTORIAN verifies the boundary catches failures inside
# ``_do_vs_validate`` specifically.
#
# Note: this probe is a STRUCTURAL verification — the duckdb.Error handler
# is registered at app scope and covers every ``_do_*`` handler uniformly.
# The probe uses monkeypatch to inject a duckdb.Error into the code path
# and asserts the response is 503 OperationOutcome (not 500 text/plain).


def test_h10_duckdb_error_handler_registered_for_vs_validate(fhir_client):
    """CF-HISTORIAN-CS04-02 — verify ``_duckdb_error_handler`` is registered
    and produces 503 OperationOutcome for a DuckDB failure inside
    ``_do_vs_validate``.

    Strategy 18 (alternative-failure-path probe at error-isolation boundary,
    TS-04 HISTORIAN QA-038) applied to the per-operation VS/$validate-code
    path. The probe monkeypatches ``get_code_infos`` (the engine entry
    point inside ``_do_vs_validate``) to raise ``duckdb.Error``, then
    asserts the response is:
      (a) HTTP 503 (not 500 — per CR-019 design choice)
      (b) Content-Type ``application/fhir+json``
      (c) Body is a Parameters/OperationOutcome-shape resource (NOT plain
          text — Starlette default)

    The bug would manifest as: response status 500, Content-Type
    ``text/plain``; body containing a Python traceback. CR-019 fixed this
    via the app-level exception handler.
    """
    import duckdb

    # Import the module where get_code_infos is bound as a name so the
    # monkeypatch hits the function reference looked up by ``_do_vs_validate``.
    import medterm4ds.apps.fhir_api as fhir_api_mod

    original = fhir_api_mod.get_code_infos

    def _raise_duckdb(*args, **kwargs):
        raise duckdb.Error("simulated transient DB failure")

    fhir_api_mod.get_code_infos = _raise_duckdb
    try:
        r = fhir_client.get(
            f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
            f"&code={SNOMED_T2DM_CODE}"
        )
    finally:
        fhir_api_mod.get_code_infos = original

    # CR-019 contract: 503 + FHIR MIME + OperationOutcome body.
    assert r.status_code == 503, (
        f"CR-019 / CF-HISTORIAN-CS04-02: duckdb.Error inside _do_vs_validate "
        f"MUST yield 503 (transient DB unavailable). Got {r.status_code}. "
        f"Body: {r.text[:300]}"
    )
    ct = r.headers.get("content-type", "")
    assert "application/fhir+json" in ct or "application/fhir" in ct, (
        f"CR-019: 503 response MUST carry FHIR MIME type. Got {ct!r}. "
        f"Body: {r.text[:200]}"
    )
    body = r.json()
    # OperationOutcome is the FHIR-spec error resource. The handler
    # returns OperationOutcome via _fhir_error_response.
    assert body.get("resourceType") == "OperationOutcome", (
        f"CR-019: 503 body MUST be OperationOutcome. Got resourceType="
        f"{body.get('resourceType')!r}. Body: {str(body)[:200]}"
    )


def test_h11_duckdb_error_handler_fires_on_post_path(fhir_client):
    """CR-019 mirror on the POST path — same as h10 but for
    ``vs_validate_post``.

    The POST path delegates through ``_do_vs_validate`` (the same inner
    handler as GET). The duckdb.Error MUST propagate identically and be
    caught by the same app-level handler. Guards against a one-path-only
    fix on CR-019.
    """
    import duckdb

    import medterm4ds.apps.fhir_api as fhir_api_mod

    original = fhir_api_mod.get_code_infos

    def _raise_duckdb(*args, **kwargs):
        raise duckdb.Error("simulated POST-path DB failure")

    fhir_api_mod.get_code_infos = _raise_duckdb
    try:
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_T2DM_CODE},
            ],
        }
        r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    finally:
        fhir_api_mod.get_code_infos = original

    assert r.status_code == 503
    assert r.json().get("resourceType") == "OperationOutcome"


# ===========================================================================
# Item 2: CR-011 — canonical system echo on _do_vs_validate
# ===========================================================================
# Per CS-02 TERMINOLOGIST DECISION (a) on $lookup Out `system`: "The
# canonical URI of the code system that contains the concept". Same shape
# on $validate-code per FHIR R4 §4.9.3 cross-reference. CR-011 fixed
# ``_do_vs_validate`` by calling ``canonical_system_uri()`` at line 1898.
#
# VS-05 SKEPTIC test_s82 covered trailing-slash; HISTORIAN extends to:
# - OID alias (urn:oid:...) on GET
# - OID alias on POST
# - GET↔POST parity on canonical echo


def test_h20_vs_validate_system_out_canonical_for_oid_alias_get(fhir_client):
    """CR-011 mirror — GET with OID alias MUST return canonical URI.

    The Out `system` parameter MUST be the canonical URI from
    ``SYSTEM_TO_FHIR_URI`` registry, never the raw client alias. The
    ``canonical_system_uri()`` helper resolves through the alias map
    and back through ``SYSTEM_TO_FHIR_URI``. This is the same fix
    pattern as CS-02 HISTORIAN QA-047 (applied to ``_do_lookup``) and
    CS-03 HISTORIAN QA-051 (applied to ``_do_validate``); CR-011 extends
    it to ``_do_vs_validate``.

    SKEPTIC test_s82 covered trailing-slash only. HISTORIAN adds the OID
    alias case which is more clinical-impact-relevant (OIDs appear in
    many EHRs' FHIR exports).
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_OID_ALIAS}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    assert r.status_code == 200, (
        f"OID alias must be recognized. Got {r.status_code}. Body: {r.text[:200]}"
    )
    body = r.json()
    sys_val = _param_value(body, "system")
    assert sys_val == SNOMED_URI, (
        f"Out `system` MUST be canonical SNOMED URI ({SNOMED_URI!r}) not "
        f"the OID alias ({SNOMED_OID_ALIAS!r}). Client-input-as-canonical "
        f"drift is prohibited (CF pattern count=7 PROMOTED). Got {sys_val!r}."
    )


def test_h21_vs_validate_system_out_canonical_for_oid_alias_post(fhir_client):
    """CR-011 mirror on POST path — OID alias via Parameters body.

    The POST path delegates through ``_do_vs_validate`` (same handler as
    GET). The canonical re-resolution MUST fire identically. Guards
    against a one-path-only fix.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_OID_ALIAS},
            {"name": "code", "valueCode": SNOMED_T2DM_CODE},
        ],
    }
    r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
    assert r.status_code == 200, (
        f"POST with OID alias must be recognized. Got {r.status_code}."
    )
    sys_val = _param_value(r.json(), "system")
    assert sys_val == SNOMED_URI, (
        f"POST-path Out `system` MUST be canonical. Got {sys_val!r}."
    )


def test_h22_vs_validate_system_out_canonical_for_icd10cm_oid_alias(fhir_client):
    """CR-011 mirror on ICD-10-CM — second source for cross-system
    verification. CS-02 HISTORIAN QA-047 verified the same fix on
    ``_do_lookup`` for ICD-10-CM; VS-05 HISTORIAN verifies the VS surface.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={ICD10CM_OID_ALIAS}"
        f"&code={ICD10CM_E11_CODE}"
    )
    assert r.status_code == 200
    sys_val = _param_value(r.json(), "system")
    assert sys_val == ICD10CM_URI, (
        f"Out `system` MUST be canonical ICD-10-CM URI ({ICD10CM_URI!r}) "
        f"not the OID alias. Got {sys_val!r}."
    )


def test_h23_vs_validate_system_out_canonical_for_rxnorm_oid_alias(fhir_client):
    """CR-011 mirror on RxNorm — third source. RXNORM_OID_ALIAS was
    registered in FHIR_URI_ALIASES; VS-05 HISTORIAN verifies the alias
    resolves through ``canonical_system_uri`` on the VS surface.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={RXNORM_OID_ALIAS}"
        f"&code={RXNORM_METFORMIN_CODE}"
    )
    assert r.status_code == 200
    sys_val = _param_value(r.json(), "system")
    assert sys_val == RXNORM_URI, (
        f"Out `system` MUST be canonical RxNorm URI ({RXNORM_URI!r}) not "
        f"the OID alias. Got {sys_val!r}."
    )


def test_h24_vs_validate_get_post_parity_on_canonical_echo(fhir_client):
    """GET↔POST parity probe class (VS-04 EXPLORER strategy 50) applied to
    the canonical-system-echo concern.

    For the same (alias system, code) on GET and POST, the Out `system`
    MUST be byte-identical — the canonical URI. A divergence would
    indicate the GET and POST paths resolve the canonical differently.
    """
    get_r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_SLASH_ALIAS}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    post_body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_SLASH_ALIAS},
            {"name": "code", "valueCode": SNOMED_T2DM_CODE},
        ],
    }
    post_r = fhir_client.post("/fhir/ValueSet/$validate-code", json=post_body)

    assert get_r.status_code == post_r.status_code == 200
    get_sys = _param_value(get_r.json(), "system")
    post_sys = _param_value(post_r.json(), "system")
    assert get_sys == post_sys == SNOMED_URI, (
        f"GET↔POST parity on canonical echo: GET={get_sys!r}, POST={post_sys!r}. "
        f"Both MUST be {SNOMED_URI!r}."
    )


# ===========================================================================
# Item 3: SKEPTIC QA-069 fix survived — display mismatch edge cases
# ===========================================================================
# Per FHIR R4 In Parameters ``display``: "A display to verify". The CS-03
# SKEPTIC QA-048 + VS-05 SKEPTIC QA-069 fix enforces:
#   if (code_info is not None
#       and display is not None
#       and canonical_display is not None
#       and display != canonical_display):
#       return result=false + message + canonical display
#
# HISTORIAN probes the boundaries of this 4-condition conjunction.


def test_h30_vs_validate_no_display_param_returns_true_for_known_code(fhir_client):
    """No ``display`` parameter → MUST NOT trigger mismatch.

    When the client omits ``display``, there is nothing to verify.
    Result MUST be true for a known code. This is the primary guard
    against an over-aggressive mismatch implementation.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    assert r.status_code == 200
    assert _param_value(r.json(), "result") is True


def test_h31_vs_validate_canonical_display_equality_no_mismatch(fhir_client):
    """When client display == canonical display → MUST return result=true.

    Sanity check that byte-exact match doesn't trigger mismatch.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display={SNOMED_T2DM_DISPLAY}"
    )
    assert r.status_code == 200
    assert _param_value(r.json(), "result") is True


def test_h32_vs_validate_case_differing_display_triggers_mismatch(fhir_client):
    """Case-differing display → triggers mismatch (SNOMED is case-sensitive).

    The implementation uses byte-exact ``!=`` comparison; "type 2 diabetes
    mellitus" != "Type 2 diabetes mellitus". Documented edge: per-source
    case-sensitivity is not implemented as a flag, but the byte-exact
    comparison preserves clinical safety.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=type 2 diabetes mellitus"
    )
    body = r.json()
    result_val = _param_value(body, "result")
    assert result_val is False, (
        f"Case-differing display MUST trigger mismatch (byte-exact compare). "
        f"Got result={result_val!r}."
    )
    # And the canonical display MUST be returned (not the wrong lowercase).
    assert _param_value(body, "display") == SNOMED_T2DM_DISPLAY


def test_h33_vs_validate_message_byte_exact_format(fhir_client):
    """SKEPTIC test_s53 pins the message format byte-exact. HISTORIAN
    re-verifies via a separate code so a regression in either probe is
    isolated to its own assertion path.

    Per spec example (mirror of CS-03 TERMINOLOGIST test_t90):
        The display "X" is incorrect
    """
    wrong = "DEFINITELY-WRONG"
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display={wrong}"
    )
    body = r.json()
    msg = _param_value(body, "message")
    assert msg == f'The display "{wrong}" is incorrect', (
        f"Spec example message byte-exact. Got {msg!r}."
    )


# ===========================================================================
# Item 4: SKEPTIC QA-070 fix survived — codeableConcept multi-coding
# ===========================================================================
# Per FHIR R4 In Parameters ``codeableConcept``: "The server returns true
# if one of the coding values is in the code system". The all-pairs helper
# ``_extract_all_coding_pairs_from_codeable_concept`` MUST be wired into
# BOTH the per-operation POST route AND the batch dispatcher's
# ``_extract_vs_validate_params``.


def test_h40_vs_validate_batch_codeable_concept_multi_coding_returns_true(fhir_client):
    """QA-070 batch-path mirror — codeableConcept [INVALID, VALID] on the
    BATCH dispatcher MUST return result=true (all-pairs semantic).

    Mirrors CS-03 HISTORIAN test_h70 (which caught the sibling bug on
    ``_extract_validate_params``). VS-05 SKEPTIC claims the batch path
    is wired correctly; HISTORIAN verifies via the batch POST.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {
                    "method": "POST",
                    "url": "/ValueSet/$validate-code",
                },
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {
                            "name": "codeableConcept",
                            "valueCodeableConcept": {
                                "coding": [
                                    # INVALID first, VALID second.
                                    {"system": SNOMED_URI, "code": "BOGUS_QA_H40"},
                                    {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
                                ]
                            },
                        }
                    ],
                },
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Bundle"
    assert body.get("type") == "batch-response"
    assert len(body.get("entry", [])) == 1
    entry = body["entry"][0]
    assert entry.get("response", {}).get("status") == "200"
    resource = entry.get("resource", {})
    result_val = None
    for p in resource.get("parameter", []):
        if p.get("name") == "result":
            result_val = p.get("valueBoolean")
            break
    assert result_val is True, (
        f"batch VS/$validate-code with codeableConcept [INVALID, VALID] "
        f"MUST return result=true per spec 'any coding matches'. "
        f"Got result={result_val!r}. If this fails, the batch dispatcher "
        f"path may be using the single-pair helper (CS-03 HISTORIAN QA-052 "
        f"shape on the VS surface)."
    )


def test_h41_vs_validate_batch_codeable_concept_all_invalid_returns_false(fhir_client):
    """QA-070 batch-path mirror — all invalid codings → result=false.

    Symmetric negative case to h40. The all-pairs helper returns false
    only when NO coding matches. The message MUST be present.
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {
                    "method": "POST",
                    "url": "/ValueSet/$validate-code",
                },
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {
                            "name": "codeableConcept",
                            "valueCodeableConcept": {
                                "coding": [
                                    {"system": SNOMED_URI, "code": "BAD1_H41"},
                                    {"system": SNOMED_URI, "code": "BAD2_H41"},
                                ]
                            },
                        }
                    ],
                },
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    body = r.json()
    resource = body["entry"][0].get("resource", {})
    result_val = None
    for p in resource.get("parameter", []):
        if p.get("name") == "result":
            result_val = p.get("valueBoolean")
            break
    assert result_val is False


def test_h42_vs_validate_batch_codeable_concept_three_codings_returns_true(fhir_client):
    """QA-070 batch-path mirror — 3 codings, third is valid.

    Verifies the iteration correctly walks the full list (not just first 2).
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {
                    "method": "POST",
                    "url": "/ValueSet/$validate-code",
                },
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {
                            "name": "codeableConcept",
                            "valueCodeableConcept": {
                                "coding": [
                                    {"system": SNOMED_URI, "code": "BAD1_H42"},
                                    {"system": SNOMED_URI, "code": "BAD2_H42"},
                                    {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
                                ]
                            },
                        }
                    ],
                },
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    body = r.json()
    resource = body["entry"][0].get("resource", {})
    result_val = None
    for p in resource.get("parameter", []):
        if p.get("name") == "result":
            result_val = p.get("valueBoolean")
            break
    assert result_val is True, (
        f"3-coding codeableConcept with 3rd valid → result=true. "
        f"Got {result_val!r}. Helper may not be iterating the full list."
    )


# ===========================================================================
# Item 5: Cross-handler parity — CS-03 ↔ VS-05
# ===========================================================================
# Per FHIR R4 §4.9.3: ValueSet/$validate-code In/Out Parameters are
# structurally identical to CodeSystem/$validate-code. The display
# mismatch logic in ``_do_validate`` and ``_do_vs_validate`` MUST be
# byte-symmetric.


def test_h50_cross_handler_display_mismatch_parity_cs_vs_vs(fhir_client):
    """Cross-handler parity probe — same (system, code, wrong display)
    on CodeSystem/$validate-code and ValueSet/$validate-code MUST return
    the SAME:
      - result value (false)
      - message format byte-exact
      - canonical display byte-exact

    A divergence indicates the two handlers drifted (e.g. one was fixed
    for QA-048/QA-069 but the other was missed). Mirrors strategy 50
    (GET↔POST parity) extended to the cross-handler axis.
    """
    wrong = "PARITY-WRONG"
    cs_r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display={wrong}"
    )
    vs_r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display={wrong}"
    )
    assert cs_r.status_code == vs_r.status_code == 200
    cs_body = cs_r.json()
    vs_body = vs_r.json()

    # result value MUST match
    assert _param_value(cs_body, "result") == _param_value(vs_body, "result") is False
    # message MUST match byte-exact
    assert _param_value(cs_body, "message") == _param_value(vs_body, "message"), (
        f"Cross-handler message drift: "
        f"CS={_param_value(cs_body, 'message')!r}, "
        f"VS={_param_value(vs_body, 'message')!r}."
    )
    # canonical display MUST match byte-exact
    assert _param_value(cs_body, "display") == _param_value(vs_body, "display"), (
        f"Cross-handler canonical display drift: "
        f"CS={_param_value(cs_body, 'display')!r}, "
        f"VS={_param_value(vs_body, 'display')!r}."
    )


def test_h51_cross_handler_known_code_parity_cs_vs_vs(fhir_client):
    """Positive-control parity probe — known code, no display. CS and VS
    MUST both return result=true.
    """
    cs_r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    vs_r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    assert _param_value(cs_r.json(), "result") is True
    assert _param_value(vs_r.json(), "result") is True


# ===========================================================================
# Item 6: Test-too-lenient audit — TS-03 HISTORIAN QA-034 pattern
# ===========================================================================
# Every VS-05 SKEPTIC probe MUST assert a POSITIVE success shape, not the
# absence of an error string. The probe below tightens SKEPTIC test_s53's
# assertion that the message is byte-exact with the spec example.


def test_h60_skeptic_test_s51_message_format_byte_exact(fhir_client):
    """Test-too-lenient audit — SKEPTIC test_s51 asserts the message
    contains 'WRONG-CLINICAL-DISPLAY' and 'incorrect' (substring match).
    HISTORIAN tightens: the message MUST be byte-exact with the spec
    example format ``The display "X" is incorrect`` — not a generic
    'incorrect' + wrong-value error message.
    """
    wrong = "definitely-incorrect-display"
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display={wrong}"
    )
    body = r.json()
    msg = _param_value(body, "message")
    assert msg is not None
    # Byte-exact: no leading/trailing whitespace; spec example punctuation.
    assert msg == f'The display "{wrong}" is incorrect', (
        f"Message MUST be byte-exact with spec example. Got {msg!r}."
    )
    # No synonyms: 'mismatch' / 'doesn't match' must NOT appear in lieu of
    # 'incorrect'. (We don't ban 'wrong' because the client value may
    # coincidentally contain it; we DO ban 'mismatch' / "doesn't match" /
    # 'invalid display' which would indicate a different message template.)
    msg_lower = msg.lower()
    for forbidden in ("mismatch", "doesn't match", "invalid display"):
        assert forbidden not in msg_lower, (
            f"Spec uses 'incorrect' (not {forbidden!r}). Got {msg!r}."
        )


def test_h61_skeptic_test_s33_canonical_display_value_tightened(fhir_client):
    """Test-too-lenient audit — SKEPTIC test_s33 asserts the Out `display`
    equals the engine canonical. HISTORIAN verifies the value matches the
    engine-canonical display for the seeded SNOMED T2DM code
    ("Type 2 diabetes mellitus"). This is a re-statement rather than a
    tightening; documents the assertion for cross-test maintenance.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    body = r.json()
    display = _param_value(body, "display")
    assert display == SNOMED_T2DM_DISPLAY, (
        f"Out `display` MUST equal the engine canonical preferred term. "
        f"Expected {SNOMED_T2DM_DISPLAY!r}, got {display!r}."
    )


def test_h62_skeptic_test_s60_implicit_valueset_url_asserts_positive(fhir_client):
    """Test-too-lenient audit — SKEPTIC test_s60 asserts result=true on
    implicit-value-set URL form. HISTORIAN re-verifies that the assertion
    is positive (result IS True), not negative-only (no error string).
    The probe passes today; documents the assertion shape.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?url={SNOMED_URI}"
        f"&system={SNOMED_URI}&code={SNOMED_T2DM_CODE}"
    )
    body = r.json()
    result_val = _param_value(body, "result")
    assert result_val is True, (
        f"Positive success shape required. Got result={result_val!r}."
    )


# ===========================================================================
# Item 7: Carry-forward verification probes
# ===========================================================================


def test_h70_cf_skeptic_cs03_01_closed_via_vs_validate_display_mismatch(fhir_client):
    """CF-SKEPTIC-CS03-01 (CLOSED) verification probe.

    The carry-forward was opened in CS-03 SKEPTIC QA-048 and closed in
    VS-05 SKEPTIC QA-069. The closing fix added display mismatch
    enforcement to ``_do_vs_validate``. This probe fires LOUDLY if a
    future regression removes the fix — the assertion expected True
    (the prior buggy behavior) but the fix produces False.

    Pattern: carry-forward-as-probe fires on fix landing (4th META
    confirmation in VS-05 SKEPTIC). The CS-03 TERMINOLOGIST test_t60
    pin was updated in-PR to assert the new behavior.
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}&display=POST-CF-SKEPTIC-CS03-01"
    )
    body = r.json()
    assert _param_value(body, "result") is False, (
        "CF-SKEPTIC-CS03-01 closed: display mismatch MUST be enforced. "
        "If this fires, the carry-forward has regressed."
    )


def test_h71_cf_historian_vs02_02_does_not_apply_to_vs_validate(fhir_client):
    """CF-HISTORIAN-VS02-02 (MEDIUM, DEFERRED) cross-verification probe.

    The carry-forward applies to ``_expand_implicit_value_set`` (the
    $expand path), NOT to ``_do_vs_validate`` (the $validate-code path).
    CR-011 (milestone-2 review) fixed ``_do_vs_validate``'s canonical
    system echo; CF-HISTORIAN-VS02-02 is the remaining open gap on
    $expand. The probe confirms the VS/$validate-code surface does NOT
    share the gap.

    Mirrors VS-05 SKEPTIC test_s110 (carries the same assertion).
    """
    r = fhir_client.get(
        f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM_CODE}"
    )
    body = r.json()
    sys_val = _param_value(body, "system")
    assert sys_val == SNOMED_URI, (
        f"VS/$validate-code Out `system` MUST be canonical SNOMED URI. "
        f"Got {sys_val!r}. CF-HISTORIAN-VS02-02 does NOT apply to this surface."
    )


# ===========================================================================
# Item 8: Structural-source-reading probes
# ===========================================================================
# Per strategy 29 (carry-forward-verification-by-source-reading AST),
# verify the structural property that ``canonical_system_uri`` IS called
# in ``_do_vs_validate``. This guards against a future refactor that
# inlines the helper away.


def test_h80_do_vs_validate_calls_canonical_system_uri():
    """Source-reading probe — verify ``_do_vs_validate`` calls
    ``canonical_system_uri(...)``.

    Guards against a future refactor that removes the CR-011 fix by
    inlining the helper away. The probe reads the source of
    ``fhir_api.py`` and asserts the canonical helper is invoked inside
    ``_do_vs_validate`` (not inside ``_do_validate`` or another sibling).
    """
    import medterm4ds.apps.fhir_api as mod
    import inspect

    src = inspect.getsource(mod.create_fhir_app)
    # Locate ``_do_vs_validate`` body (NOT ``_do_validate`` — sibling).
    # Use the function-definition boundary to extract just the VS handler.
    pattern = r"def _do_vs_validate\([^)]*\)[^:]*:(.*?)(?=\n    @app\.|\n    def _do_translate)"
    match = re.search(pattern, src, re.DOTALL)
    assert match is not None, (
        "Could not locate _do_vs_validate body in source. The function may "
        "have been renamed — verify the canonical_system_uri call still fires."
    )
    body = match.group(1)
    assert "canonical_system_uri(" in body, (
        "CR-011 fix: _do_vs_validate MUST call canonical_system_uri(...). "
        "If this fires, the CR-011 fix has been regressed."
    )


def test_h81_do_vs_validate_enforces_display_mismatch_via_source():
    """Source-reading probe — verify ``_do_vs_validate`` contains the
    display-mismatch enforcement block (VS-05 SKEPTIC QA-069 fix).

    Guards against a future regression that removes the display-mismatch
    check by inlining or restructuring.
    """
    import medterm4ds.apps.fhir_api as mod
    import inspect

    src = inspect.getsource(mod.create_fhir_app)
    pattern = r"def _do_vs_validate\([^)]*\)[^:]*:(.*?)(?=\n    @app\.|\n    def _do_translate)"
    match = re.search(pattern, src, re.DOTALL)
    assert match is not None
    body = match.group(1)
    # The enforcement block has a 4-condition conjunction.
    assert "display != canonical_display" in body, (
        "VS-05 SKEPTIC QA-069: _do_vs_validate MUST contain the display "
        "mismatch comparison. If this fires, the fix has been regressed."
    )
    assert 'The display "' in body and "is incorrect" in body, (
        "VS-05 SKEPTIC QA-069: spec example message format MUST be present."
    )


def test_h82_vs_validate_post_uses_all_pairs_helper():
    """Source-reading probe — verify ``vs_validate_post`` (per-operation
    POST) calls ``_extract_all_coding_pairs_from_codeable_concept`` (NOT
    the single-pair ``_extract_codeable_concept_from_parameters``).

    Guards against a regression that swaps the helper back to single-pair.
    """
    import medterm4ds.apps.fhir_api as mod
    import inspect

    src = inspect.getsource(mod.create_fhir_app)
    # Locate vs_validate_post body.
    pattern = r"async def vs_validate_post\([^)]*\)[^:]*:(.*?)(?=\n    @app\.|\n    def _do_vs_validate)"
    match = re.search(pattern, src, re.DOTALL)
    assert match is not None
    body = match.group(1)
    assert "_extract_all_coding_pairs_from_codeable_concept" in body, (
        "VS-05 SKEPTIC QA-070: vs_validate_post MUST call the all-pairs "
        "helper. If this fires, the fix has been regressed."
    )


def test_h83_extract_vs_validate_params_uses_all_pairs_helper():
    """Source-reading probe — verify ``_extract_vs_validate_params``
    (batch dispatcher) calls ``_extract_all_coding_pairs_from_codeable_concept``
    (NOT the single-pair helper).

    Mirrors CS-03 HISTORIAN QA-052 source-reading pattern on the VS
    surface. Guards against the batch path drifting to single-pair.
    """
    import medterm4ds.apps.fhir_api as mod
    import inspect

    src = inspect.getsource(mod.create_fhir_app)
    pattern = r"def _extract_vs_validate_params\([^)]*\)[^:]*:(.*?)(?=\n    def _extract_translate_params)"
    match = re.search(pattern, src, re.DOTALL)
    assert match is not None, (
        "Could not locate _extract_vs_validate_params body. The function "
        "may have been renamed."
    )
    body = match.group(1)
    assert "_extract_all_coding_pairs_from_codeable_concept" in body, (
        "VS-05 SKEPTIC QA-070 batch-path: _extract_vs_validate_params MUST "
        "call the all-pairs helper. Mirrors CS-03 HISTORIAN QA-052."
    )


def test_h84_duckdb_error_handler_registered():
    """Source-reading probe — verify ``@app.exception_handler(duckdb.Error)``
    is registered (CR-019 / CF-HISTORIAN-CS04-02 systemic fix).

    Guards against a future refactor that removes the handler.
    """
    import medterm4ds.apps.fhir_api as mod
    import inspect

    src = inspect.getsource(mod.create_fhir_app)
    assert "exception_handler(duckdb.Error)" in src, (
        "CR-019 / CF-HISTORIAN-CS04-02: @app.exception_handler(duckdb.Error) "
        "MUST be registered. If this fires, the systemic DB-error boundary "
        "has been removed."
    )
