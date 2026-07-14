"""HISTORIAN probes for chunk CM-02 (ConceptMap $translate Operation).

Source: https://build.fhir.org/conceptmap-operation-translate.html
Canonical R4 $translate operation:
    https://hl7.org/fhir/R4/conceptmap-operation-translate.html
Canonical R4 ConceptMapEquivalence closed enum:
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html

HISTORIAN lens (pattern-match against prior bug patterns). Carry-forwards
from SKEPTIC iteration (CM-02_SKEPTIC_qa_handoff.md):

  * CF-CM02-01 (coding/codeableConcept silent-drop on $translate POST):
    SKEPTIC noted ``_extract_coding_from_parameters`` is wired into
    $lookup, CodeSystem/$validate-code, ValueSet/$validate-code,
    $subsumes — but NOT into $translate. This is the 7th potential
    instance of "silent-wrong-answer on alternative parameter
    encodings" pattern (count=6 PROMOTED in VS-05 SKEPTIC QA-070).
    Verify by source-reading ``_do_translate`` and ``translate_post``.
  * Equivalence vocabulary audit (post-milestone-3 consolidation):
    ``engines/fhir/equivalence.py`` is the single source of truth.
    Every match.equivalence value from ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE``?
  * Canonical-URI helper usage on _do_translate (CR-012, CR-025):
    Verify both scalar and codeableConcept branches use
    ``canonical_system_uri()``.
  * Systemic duckdb.Error gap (CF-HISTORIAN-CS04-02):
    App-level handler provides boundary. Verify on _do_translate.
  * Test-too-lenient:
    Re-audit SKEPTIC's 36 CM-02 probes.

HISTORIAN also probes (methodology contributions):
  * Cross-handler helper-wiring consistency (TS-02 EXPLORER QA-028
    pattern class) — when an operation handler is added/extended,
    audit the batch dispatcher's sibling extractor.
  * Equivalence directionality (CM-01 SKEPTIC-001 — source-is-narrower
    → R4 wider) preserved through canonical module.
  * Closed-enum membership invariant via module-load assertion
    (CF-HISTORIAN-VS01-01 — structural fix applied).
  * Test-too-lenient: SKEPTIC test_s42/test_s43 use negative-only
    shape ("must be 400 + OperationOutcome") — apply TS-03 HISTORIAN
    QA-034 tightening lens.
"""

from __future__ import annotations

import inspect
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
    build_parameters_translate,
    _fhir_equivalence_from_relationship,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URI_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.96"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
CONCEPTMAP_URL = "http://medterm4ds.org/fhir/ConceptMap/snomed-to-icd10"


def _find_param(body: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Return the first ``parameter`` entry with ``name == name``, else None."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            return p
    return None


# ===========================================================================
# Lens 1: CF-CM02-01 — coding/codeableConcept silent-drop on $translate
# Pattern-match against TS-02 HISTORIAN QA-022/QA-023, TS-02 EXPLORER
# QA-026/QA-028, CS-04 SKEPTIC QA-053. SKEPTIC iteration flagged this
# but did not promote to bug (deferred as feature enhancement). HISTORIAN
# confirms via source-reading that ``_extract_coding_from_parameters``
# is NOT wired into ``translate_post`` and ``_extract_translate_params``
# (batch dispatcher).
# ===========================================================================


def test_h10_cf_cm02_01_source_audit_translate_post_does_not_call_coding_extractor():
    """CF-CM02-01 (HISTORIAN confirmation via source-reading).

    ``translate_post`` (line 1986-1995 in apps/fhir_api.py) MUST be
    source-read to confirm it does NOT call
    ``_extract_coding_from_parameters`` or
    ``_extract_codeable_concept_from_parameters``. The handler uses
    ``_parse_parameters`` (scalar-only — TS-02 HISTORIAN QA-022) and
    silently drops the spec-listed ``coding`` and ``codeableConcept``
    alternative encodings (per FHIR R4 $translate In Parameters:
    https://hl7.org/fhir/R4/conceptmap-operation-translate.html).

    This is the 7th instance of "silent-wrong-answer on alternative
    parameter encodings" pattern (count=6 PROMOTED in VS-05 SKEPTIC
    QA-070 — $lookup, CodeSystem/$validate-code (coding + codeableConcept),
    ValueSet/$validate-code (coding + codeableConcept), $subsumes (codingA
    + codingB), $expand (valueSet)). The pattern persists despite
    PROMOTION — every new operation accepting alternative encodings
    inherits the drift class until a structural fix lands.

    Pattern recurrence: silent-wrong-answer on alternative parameter
    encodings count=7 (was 6 PROMOTED).
    """
    src = inspect.getsource(fhir_api.create_fhir_app)
    # Locate translate_post
    assert "async def translate_post" in src, (
        "translate_post not found in create_fhir_app source"
    )
    # Find the translate_post body slice
    start = src.index("async def translate_post")
    # Find the next ``def `` at the same indentation level OR a decorator
    end_markers = ["\n    @app.post", "\n    @app.get", "\n    def _do_translate"]
    end = len(src)
    for m in end_markers:
        idx = src.find(m, start + 1)
        if idx != -1 and idx < end:
            end = idx
    translate_post_src = src[start:end]

    # The translate_post body MUST NOT call _extract_coding_from_parameters.
    assert "_extract_coding_from_parameters" not in translate_post_src, (
        "translate_post calls _extract_coding_from_parameters — CF-CM02-01 "
        "appears RESOLVED (coding alternative encoding wired in). Update "
        "SKEPTIC test_s42 to assert the 200 path."
    )
    # The translate_post body MUST NOT call
    # _extract_codeable_concept_from_parameters.
    assert "_extract_codeable_concept_from_parameters" not in translate_post_src, (
        "translate_post calls _extract_codeable_concept_from_parameters — "
        "CF-CM02-01 codeableConcept branch appears RESOLVED. Update "
        "SKEPTIC test_s43 to assert the 200 path."
    )
    # The translate_post body MUST NOT call the all-pairs helper either
    # (per-op POST $translate has single-coding semantic per spec).
    assert "_extract_all_coding_pairs_from_codeable_concept" not in translate_post_src, (
        "translate_post calls _extract_all_coding_pairs_from_codeable_concept — "
        "unexpected; the $translate spec implies single-coding semantic "
        "(per TS-02 TERMINOLOGIST QA-029 / CS-03 SKEPTIC QA-049 distinction)."
    )


def test_h11_cf_cm02_01_source_audit_batch_dispatcher_does_not_call_coding_extractor():
    """CF-CM02-01 (HISTORIAN confirmation via batch dispatcher source-reading).

    The batch dispatcher's ``_extract_translate_params`` (lines 1328-1342)
    is the sibling extractor of ``translate_post``. Cross-handler
    helper-wiring consistency (TS-02 EXPLORER QA-028 pattern class)
    requires the audit to extend to the batch path.

    The batch dispatcher MUST be source-read to confirm it does NOT call
    ``_extract_coding_from_parameters`` or
    ``_extract_codeable_concept_from_parameters`` either. This is NOT a
    divergence from the per-operation POST (both are buggy in the same
    way — opposite of CS-03 HISTORIAN QA-052 where per-op HAD the helper
    but batch did NOT). The structural fix candidate (when CF-CM02-01
    lands) MUST wire the helper into BOTH paths simultaneously.
    """
    src = inspect.getsource(fhir_api.create_fhir_app)
    assert "def _extract_translate_params" in src, (
        "_extract_translate_params not found"
    )
    start = src.index("def _extract_translate_params")
    # Body ends at the next def at the same indentation
    end = src.find("\n    def ", start + 1)
    if end == -1:
        end = len(src)
    extract_translate_src = src[start:end]

    assert "_extract_coding_from_parameters" not in extract_translate_src, (
        "_extract_translate_params calls _extract_coding_from_parameters "
        "— batch path appears RESOLVED for coding alt-encoding."
    )
    assert "_extract_codeable_concept_from_parameters" not in extract_translate_src, (
        "_extract_translate_params calls "
        "_extract_codeable_concept_from_parameters — batch path appears "
        "RESOLVED for codeableConcept alt-encoding."
    )


def test_h12_cf_cm02_01_behavioral_probe_coding_only_body_currently_400(fhir_client):
    """CF-CM02-01 (behavioral confirmation — carry-forward-as-probe pattern).

    POST $translate with a ``coding`` parameter (per FHIR R4 In Parameters:
    https://hl7.org/fhir/R4/conceptmap-operation-translate.html — ``coding``
    0..1 Coding). The current implementation silently drops it and falls
    through to the 400 path.

    Per the carry-forward-as-probe pattern (CS-03 TERMINOLOGIST
    methodology), this probe asserts the CURRENT behavior so that when
    CF-CM02-01 lands, the probe MUST be updated to assert 200 + Parameters
    + at least one match (positive success-shape per TS-03 HISTORIAN
    QA-034 / VS-01 HISTORIAN test_h60 tightening methodology).

    Distinct from SKEPTIC test_s42: HISTORIAN asserts the source-reading
    invariant in test_h10 AND the behavioral invariant in test_h12 — the
    pair guards against silent drift in either direction (source fix
    without probe update, or probe update without source fix).
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {
                        "system": SNOMED_URI,
                        "code": "44054006",
                    },
                },
                {
                    "name": "targetsystem",
                    "valueUri": ICD10CM_URI,
                },
            ],
        },
    )
    # CF-CM02-01 current behavior: 400 (coding silently dropped).
    # When the helper is wired, update this assertion to 200 + Parameters.
    assert r.status_code == 400, (
        f"POST $translate with coding-only body — CF-CM02-01 current "
        f"behavior is 400 (coding silently dropped). If this assertion "
        f"fails because the status is now 200, CF-CM02-01 has been "
        f"RESOLVED — update SKEPTIC test_s42 + this probe to assert 200 "
        f"+ Parameters + at least one match. Got {r.status_code}: {r.text}"
    )


def test_h13_cf_cm02_01_behavioral_probe_codeableconcept_body_currently_400(fhir_client):
    """CF-CM02-01 (codeableConcept sibling — carry-forward-as-probe).

    POST $translate with a ``codeableConcept`` parameter (per FHIR R4 In
    Parameters: ``codeableConcept`` 0..1 CodeableConcept). Same silent-drop
    shape as test_h12. Sibling probe of test_h12; both must be updated
    together when CF-CM02-01 lands.
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
                            {
                                "system": SNOMED_URI,
                                "code": "44054006",
                            }
                        ]
                    },
                },
                {
                    "name": "targetsystem",
                    "valueUri": ICD10CM_URI,
                },
            ],
        },
    )
    assert r.status_code == 400, (
        f"POST $translate with codeableConcept body — CF-CM02-01 current "
        f"behavior is 400 (codeableConcept silently dropped). If this "
        f"assertion fails because the status is now 200, CF-CM02-01 has "
        f"been RESOLVED — update SKEPTIC test_s43 + this probe to assert "
        f"200 + Parameters. Got {r.status_code}: {r.text}"
    )


# ===========================================================================
# Lens 2: Equivalence vocabulary audit (post-milestone-3 consolidation)
# CF-HISTORIAN-VS01-01 RESOLVED via CR-024 — verify the canonical module
# emits only R4 values AND every engine vocabulary token resolves.
# ===========================================================================


def test_h20_canonical_module_emits_only_r4_values():
    """Equivalence vocabulary audit — CF-HISTORIAN-VS01-01 RESOLVED.

    The canonical module at ``engines/fhir/equivalence.py`` MUST emit
    only values from ``FHIR_R4_CONCEPT_MAP_EQUIVALENCE`` (10 values per
    https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html:
    relatedto | equivalent | equal | wider | subsumes | narrower |
    specializes | inexact | unmatched | disjoint).

    The module-load assertion (lines 125-132 of equivalence.py)
    enforces this invariant at import time. HISTORIAN confirms the
    invariant holds by re-deriving the membership directly.
    """
    emitted = set(INTERNAL_REL_TO_FHIR_EQUIVALENCE.values())
    drift = emitted - FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    assert not drift, (
        f"Canonical equivalence module emits values outside R4 enum: {drift}. "
        f"CF-HISTORIAN-VS01-01 regression — module-load assertion should "
        f"have caught this at import time."
    )


def test_h21_canonical_module_resolves_all_engine_vocab():
    """Equivalence vocabulary audit — every engine token resolves.

    The engine emits at minimum 6 tokens (per the docstring at
    equivalence.py:48-53): ``equivalent``,
    ``source-is-narrower-than-target``,
    ``source-is-broader-than-target``, ``related-to``,
    ``not-translated``, ``unmatched``. Every token MUST resolve to an
    R4 value via the canonical module.

    Reference: AGENTS.md Architecture Drift Log entry for CM-01
    SKEPTIC-002 (``not-translated`` semantic fix).
    """
    engine_tokens = {
        "equivalent",
        "source-is-narrower-than-target",
        "source-is-broader-than-target",
        "related-to",
        "not-translated",
        "unmatched",
    }
    for tok in engine_tokens:
        result = fhir_equivalence(tok)
        assert result in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
            f"Engine token {tok!r} resolved to {result!r} which is NOT in "
            f"the R4 closed enum. CF-HISTORIAN-VS01-01 regression on the "
            f"engine-vocab translation surface."
        )


def test_h22_canonical_module_directionality_per_r4_spec():
    """Directionality (CM-01 SKEPTIC-001) preserved through canonical module.

    The direction-sensitive keys per R4 spec:
      * ``source-is-narrower-than-target`` ⇒ target is wider ⇒ R4 ``wider``
      * ``source-is-broader-than-target`` ⇒ target is narrower ⇒ R4 ``narrower``

    The prior responses.py map had these inverted (CM-01 SKEPTIC-001).
    The canonical module (post-CR-024) inherits the fix.
    """
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-narrower-than-target"] == "wider", (
        "Directionality drift: source-is-narrower-than-target MUST map to "
        "wider (target is WIDER in meaning than the source per R4 spec). "
        "CM-01 SKEPTIC-001 regression."
    )
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["source-is-broader-than-target"] == "narrower", (
        "Directionality drift: source-is-broader-than-target MUST map to "
        "narrower (target is NARROWER in meaning than the source per R4 "
        "spec). CM-01 SKEPTIC-001 regression."
    )
    # CM-01 SKEPTIC-002 semantic fix: not-translated ⇒ unmatched (NOT equivalent)
    assert INTERNAL_REL_TO_FHIR_EQUIVALENCE["not-translated"] == "unmatched", (
        "Semantic drift: not-translated MUST map to unmatched (R4 catch-all "
        "for 'no mapping'). The prior outputs/fhir.py mapped to 'equivalent' "
        "— silent-wrong-answer. CM-01 SKEPTIC-002 regression."
    )


def test_h23_helper_uses_canonical_module():
    """Cross-helper parity (CR-024) — both ``fhir_equivalence`` (canonical)
    and ``_fhir_equivalence_from_relationship`` (responses.py wrapper)
    MUST delegate to the same canonical module.

    Without this delegation, the two surfaces ($translate HTTP and
    ConceptMap export) could drift again (CF-HISTORIAN-VS01-01 shape).
    """
    # Both helpers MUST agree on every engine token
    engine_tokens = [
        "equivalent",
        "source-is-narrower-than-target",
        "source-is-broader-than-target",
        "related-to",
        "not-translated",
        "unmatched",
        None,  # null/empty relationship → relatedto catch-all
        "unknown-relationship-token",  # unknown → relatedto catch-all
    ]
    for tok in engine_tokens:
        canonical_val = fhir_equivalence(tok)
        wrapper_val = _fhir_equivalence_from_relationship(tok)
        assert canonical_val == wrapper_val, (
            f"Helper divergence on token {tok!r}: canonical={canonical_val!r}, "
            f"wrapper={wrapper_val!r}. CR-024 regression — cross-module "
            f"parallel-map drift should be structurally impossible."
        )


def test_h24_match_equivalence_value_in_r4_enum(fhir_client):
    """Equivalence vocabulary audit (behavioral) — every match.equivalence
    value emitted by ``build_parameters_translate`` MUST be in the R4 closed
    enum.

    Pinned via the conformance fixture: a known mapping (SNOMED 44054006 →
    ICD-10-CM E11 via same-CUI) produces one match with
    ``equivalence=equivalent``. The closed-enum membership is verified
    per-response, not just per-module-load.
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
    for m in matches:
        parts = m.get("part", [])
        for part in parts:
            if part.get("name") == "equivalence":
                val = part.get("valueCode")
                assert val in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                    f"match.equivalence value {val!r} NOT in R4 closed enum. "
                    f"CF-HISTORIAN-VS01-01 regression on the runtime surface."
                )


# ===========================================================================
# Lens 3: Canonical-URI helper usage on _do_translate (CR-012, CR-025)
# ===========================================================================


def test_h30_cr012_resolved_do_translate_uses_canonical_helper():
    """CR-012 (milestone-2 review) RESOLVED — source-reading confirmation.

    ``_do_translate`` (line 1997-2030) MUST call
    ``canonical_system_uri(source_uri, source=source)`` before passing
    the URI to ``build_parameters_translate``. Without this, the Out
    ``match[].source.system`` field echoes the client-supplied
    ``source_uri`` verbatim — including aliases (urn:oid:...) and
    trailing-slash variants.

    HISTORIAN confirms via source-reading that the call is present
    (SKEPTIC verified behaviorally in test_s25, test_s26, test_s90).
    """
    src = inspect.getsource(fhir_api.create_fhir_app)
    assert "def _do_translate(" in src, "_do_translate not found"
    start = src.index("def _do_translate(")
    end = src.find("\n    # -- ", start + 1)  # next section header
    if end == -1:
        end = len(src)
    do_translate_src = src[start:end]

    assert "canonical_system_uri(" in do_translate_src, (
        "_do_translate does NOT call canonical_system_uri — CR-012 "
        "regression. The Out match[].source.system would echo client "
        "input verbatim."
    )


def test_h31_cr012_resolved_alias_input_resolves_to_canonical(fhir_client):
    """CR-012 RESOLVED — behavioral probe on alias input.

    POST $translate with the urn:oid alias for SNOMED CT
    (urn:oid:2.16.840.1.113883.6.96). The Out ``match[].source.system``
    MUST be the canonical URI (http://snomed.info/sct), NOT the alias.
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
    assert r.status_code == 200, (
        f"POST $translate with urn:oid alias — expected 200; got "
        f"{r.status_code}: {r.text}"
    )
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert matches, "Expected at least one match for SNOMED 44054006"
    for m in matches:
        parts = m.get("part", [])
        source_part = next(
            (p for p in parts if p.get("name") == "source"), None
        )
        assert source_part is not None, "match.source part missing"
        source_coding = source_part.get("valueCoding", {})
        source_system = source_coding.get("system")
        assert source_system == SNOMED_URI, (
            f"CR-012 regression: match.source.system echoed alias verbatim "
            f"({source_system!r}); expected canonical {SNOMED_URI!r}."
        )


def test_h32_cr012_resolved_trailing_slash_input_resolves_to_canonical(fhir_client):
    """CR-012 RESOLVED — behavioral probe on trailing-slash input.

    POST $translate with trailing-slash SNOMED URI
    (http://snomed.info/sct/). The Out ``match[].source.system`` MUST
    be the canonical URI without trailing slash.
    """
    r = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI_TRAILING_SLASH},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": ICD10CM_URI},
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    matches = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert matches
    for m in matches:
        parts = m.get("part", [])
        source_part = next((p for p in parts if p.get("name") == "source"), None)
        assert source_part is not None
        source_system = source_part.get("valueCoding", {}).get("system")
        assert source_system == SNOMED_URI, (
            f"CR-012 regression: trailing-slash input leaked to Out "
            f"match.source.system ({source_system!r}); expected canonical "
            f"{SNOMED_URI!r}."
        )


def test_h33_target_system_uri_also_canonical(fhir_client):
    """CR-012 extended — target system URI canonicalization.

    The Out ``match[].concept.system`` field is derived from the engine's
    target source name via ``system_to_fhir_uri``. Verify the value is
    the canonical ICD-10-CM URI (http://hl7.org/fhir/sid/icd-10-cm),
    not a raw SAB label.

    Cross-cutting: TS-01 TERMINOLOGIST QA-012 (HCPCS URI drift),
    CS-01 SKEPTIC QA-043 (canonical_system raw SAB), CR-012 (source
    side canonical). HISTORIAN extends the audit to the target side.
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
    assert matches, "Expected at least one match"
    for m in matches:
        parts = m.get("part", [])
        concept_part = next(
            (p for p in parts if p.get("name") == "concept"), None
        )
        assert concept_part is not None, "match.concept part missing"
        concept_system = concept_part.get("valueCoding", {}).get("system")
        assert concept_system == ICD10CM_URI, (
            f"Target system URI drift: match.concept.system = "
            f"{concept_system!r}; expected canonical {ICD10CM_URI!r}."
        )


# ===========================================================================
# Lens 4: Systemic duckdb.Error boundary (CF-HISTORIAN-CS04-02)
# ===========================================================================


def test_h40_cf_historian_cs04_02_resolved_systemic_handler_registered():
    """CF-HISTORIAN-CS04-02 RESOLVED — systemic duckdb.Error handler.

    The ``@app.exception_handler(duckdb.Error)`` registered at lines
    625-632 of apps/fhir_api.py provides a SYSTEMIC boundary for every
    per-operation ``_do_*`` handler. Transient DuckDB operational
    failures emit a 503 OperationOutcome per GLOBAL_RULES.md "Silent
    Fallbacks" (use the narrowest exception type).

    HISTORIAN confirms via source-reading that the handler is present.
    The per-operation boundary is NOT required (the systemic handler
    catches it).
    """
    src = inspect.getsource(fhir_api.create_fhir_app)
    assert "@app.exception_handler(duckdb.Error)" in src, (
        "Systemic duckdb.Error handler not registered. CF-HISTORIAN-CS04-02 "
        "regression — transient DuckDB failures would propagate to "
        "Starlette's default 500 with text/plain body."
    )
    # Also verify the handler returns a FHIR-conformant 503 response
    assert "_fhir_error_response" in src, (
        "_fhir_error_response helper not found — required for FHIR-conformant "
        "OperationOutcome body on the duckdb.Error path."
    )


def test_h41_cf_historian_cs04_02_handler_emits_503_for_transient_error(
    fhir_client, monkeypatch
):
    """CF-HISTORIAN-CS04-02 RESOLVED — behavioral confirmation.

    Inject a transient duckdb.Error into the engine path and verify the
    handler emits a 503 OperationOutcome with the correct Content-Type.
    This is the alternative-failure-path probe pattern (TS-04 HISTORIAN
    QA-038 methodology) applied at the systemic boundary.

    Note: ``_do_translate`` calls the engine-instance method
    ``engine.get_code_mappings`` (defined on the LocalDuckDBEngine via
    the _MappingOps mixin). Monkeypatching the engine instance method
    is the most direct way to inject the failure without depending on
    which service function delegates where.
    """
    import duckdb

    engine = fhir_client.app.state.engine
    original = engine.get_code_mappings

    def _raise_duckdb_error(self_inner, *args, **kwargs):
        raise duckdb.Error("simulated transient DuckDB failure")

    # Bind the raising function as a method-like callable
    import types

    raising_method = types.MethodType(_raise_duckdb_error, engine)
    monkeypatch.setattr(engine, "get_code_mappings", raising_method)
    try:
        r = fhir_client.get(
            "/fhir/ConceptMap/$translate",
            params=[
                ("system", SNOMED_URI),
                ("code", "44054006"),
                ("targetsystem", ICD10CM_URI),
            ],
        )
        assert r.status_code == 503, (
            f"Systemic duckdb.Error handler did NOT fire — expected 503; "
            f"got {r.status_code}: {r.text}"
        )
        assert r.headers["content-type"].startswith("application/fhir+json"), (
            f"Content-Type drift on 503 path: {r.headers['content-type']!r}; "
            f"expected application/fhir+json."
        )
        body = r.json()
        assert body.get("resourceType") == "OperationOutcome", (
            f"resourceType drift on 503 path: {body.get('resourceType')!r}; "
            f"expected OperationOutcome."
        )
    finally:
        monkeypatch.setattr(engine, "get_code_mappings", original)


# ===========================================================================
# Lens 5: Test-too-lenient audit (TS-03 HISTORIAN QA-034 methodology)
# Re-audit SKEPTIC's 36 CM-02 probes. Apply the rule: input-recognition
# probes MUST assert the POSITIVE success shape, not just absence of one
# error string. For CF-CM02-01 carry-forward probes, assert CURRENT
# behavior with explicit CF-RESOLVED transition notes.
# ===========================================================================


def _read_skeptic_probe_source() -> str:
    """Read the SKEPTIC probe file source for source-reading audits.

    The conformance tests package is not importable via
    ``tests.fhir_conformance.test_cm02_skeptic`` because pytest discovers
    test files via filesystem walk, not via package import. File reading
    is the durable alternative.
    """
    from pathlib import Path

    p = Path(__file__).parent / "test_cm02_skeptic.py"
    return p.read_text()


def _extract_function_source(src: str, func_name: str) -> str:
    """Extract a single ``def <func_name>(...)`` body from a source string.

    Returns the source slice from ``def <func_name>`` to the next
    top-level ``def `` or end-of-string.
    """
    marker = f"def {func_name}("
    start = src.index(marker)
    # Find end: next top-level def at column 0
    end = src.find("\ndef ", start + 1)
    if end == -1:
        end = len(src)
    return src[start:end]


def test_h50_skeptic_test_s42_uses_cf_carry_forward_pattern_correctly():
    """Test-too-lenient audit — SKEPTIC test_s42.

    SKEPTIC test_s42 (POST $translate with coding-only body) asserts:
    (1) status == 400, (2) Content-Type starts with application/fhir+json,
    (3) body.resourceType == OperationOutcome.

    HISTORIAN audit: this is NOT test-too-lenient because the assertion
    IS positive (status + Content-Type + resourceType). The probe is a
    carry-forward-as-probe (CS-03 TERMINOLOGIST methodology) — it asserts
    CURRENT behavior with explicit transition notes ("When the fix lands,
    this probe MUST be tightened to assert 200 + Parameters").
    """
    src = _read_skeptic_probe_source()
    s42 = _extract_function_source(src, "test_s42_post_coding_only_body_silently_dropped_current_behavior")
    assert "== 400" in s42, (
        "test_s42 lacks the status == 400 assertion — CF-CM02-01 probe shape drift."
    )
    # The probe MUST reference the carry-forward pattern (when fix lands, tighten)
    assert "tightened" in s42.lower() or "200" in s42, (
        "test_s42 lacks the carry-forward transition note ('tighten to 200 "
        "+ Parameters when fix lands'). The probe should be load-bearing — "
        "when CF-CM02-01 lands, the probe MUST be updated."
    )


def test_h51_skeptic_test_s43_carries_matching_pattern():
    """Test-too-lenient audit — SKEPTIC test_s43 (codeableConcept).

    Same audit shape as test_h50 but on the codeableConcept sibling.
    """
    src = _read_skeptic_probe_source()
    s43 = _extract_function_source(
        src, "test_s43_post_codeableconcept_body_silently_dropped_current_behavior"
    )
    assert "== 400" in s43, "test_s43 lacks the status == 400 assertion."


def test_h52_skeptic_test_s50_uses_positive_success_shape_not_negative_only():
    """Test-too-lenient audit — SKEPTIC test_s50 (url param).

    SKEPTIC test_s50 asserts status == 200 (the url param is accepted
    without 500 — spec-compatibility fallback). This IS the positive
    success shape (200 + the request was processed). NOT test-too-lenient
    per TS-03 HISTORIAN QA-034.
    """
    src = _read_skeptic_probe_source()
    s50 = _extract_function_source(
        src, "test_s50_conceptmap_url_param_accepted_current_behavior"
    )
    assert "== 200" in s50, (
        "test_s50 does NOT assert status == 200 — test-too-lenient per "
        "TS-03 HISTORIAN QA-034 (negative-only assertions give false-"
        "positive passes on real bugs)."
    )


def test_h53_skeptic_test_s90_parametrized_over_three_input_shapes():
    """Test-too-lenient audit — SKEPTIC test_s90 (canonical URI).

    SKEPTIC test_s90 is parametrized over 3 inputs (canonical, urn:oid
    alias, trailing-slash). HISTORIAN confirms via source-reading that
    all 3 inputs are exercised and each produces the canonical URI in
    Out match[].source.system.
    """
    src = _read_skeptic_probe_source()
    # test_s90 is parametrized — the decorator appears BEFORE the def.
    # Look at the slice from the parametrize decorator THROUGH the
    # function body to verify all 3 input shapes are covered.
    s90_marker_idx = src.index("def test_s90_translate_canonical_source_system_uri")
    pre = src[:s90_marker_idx]
    # Find the LAST @pytest.mark.parametrize before test_s90
    param_idx = pre.rfind("@pytest.mark.parametrize")
    assert param_idx != -1, (
        "test_s90 is NOT parametrized via @pytest.mark.parametrize — "
        "test-too-lenient (single-input probes miss edge cases)."
    )
    # Slice from the decorator to ~2KB after the def
    full_text = src[param_idx:param_idx + 3000]
    assert "urn:oid" in full_text or "SNOMED_URI_OID_ALIAS" in full_text, (
        "test_s90 parametrize block does NOT cover the urn:oid alias shape — "
        "single-input probe misses the CR-012 alias-resolution invariant."
    )
    assert (
        "trailing_slash" in full_text.lower()
        or "trailing-slash" in full_text.lower()
        or "SNOMED_URI_TRAILING_SLASH" in full_text
    ), (
        "test_s90 parametrize block does NOT cover the trailing-slash shape."
    )


# ===========================================================================
# Lens 6: Cross-handler GET↔POST parity (CS-05 EXPLORER methodology)
# ===========================================================================


def test_h60_get_post_parity_on_translate(fhir_client):
    """GET↔POST parity — CS-05 EXPLORER cross-operation-canonical-agreement
    pattern extended to CM-02.

    GET and POST $translate MUST produce identical clinical content for
    the same (system, code, targetsystem) input. The match count,
    equivalence values, and target codes MUST be byte-exact.

    SKEPTIC test_s110 covers this; HISTORIAN extends with parametrization
    across systems (SNOMED → ICD-10-CM is the only seeded cross-system
    mapping today; HISTORIAN also probes SNOMED → RxNorm which has no
    crosswalk in the fixture to verify the no-match parity).
    """
    # SNOMED 44054006 → ICD-10-CM (seeded same-CUI match)
    r_get = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    r_post = fhir_client.post(
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
    assert r_get.status_code == r_post.status_code == 200

    body_get = r_get.json()
    body_post = r_post.json()

    matches_get = [p for p in body_get.get("parameter", []) if p.get("name") == "match"]
    matches_post = [p for p in body_post.get("parameter", []) if p.get("name") == "match"]
    assert len(matches_get) == len(matches_post), (
        f"GET/POST match count divergence: GET={len(matches_get)}, "
        f"POST={len(matches_post)}."
    )

    for m_get, m_post in zip(matches_get, matches_post):
        equiv_get = next(
            (p.get("valueCode") for p in m_get.get("part", []) if p.get("name") == "equivalence"), None
        )
        equiv_post = next(
            (p.get("valueCode") for p in m_post.get("part", []) if p.get("name") == "equivalence"), None
        )
        assert equiv_get == equiv_post, (
            f"GET/POST equivalence divergence: GET={equiv_get!r}, POST={equiv_post!r}."
        )

        concept_get = next(
            (p.get("valueCoding", {}).get("code") for p in m_get.get("part", []) if p.get("name") == "concept"), None
        )
        concept_post = next(
            (p.get("valueCoding", {}).get("code") for p in m_post.get("part", []) if p.get("name") == "concept"), None
        )
        assert concept_get == concept_post, (
            f"GET/POST target code divergence: GET={concept_get!r}, POST={concept_post!r}."
        )


def test_h61_get_post_parity_on_no_match(fhir_client):
    """GET↔POST parity on no-match case (SNOMED → RxNorm has no crosswalk
    in the conformance fixture).
    """
    r_get = fhir_client.get(
        "/fhir/ConceptMap/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", RXNORM_URI),
        ],
    )
    r_post = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": RXNORM_URI},
            ],
        },
    )
    assert r_get.status_code == r_post.status_code == 200

    body_get = r_get.json()
    body_post = r_post.json()
    result_get = next(
        (p.get("valueBoolean") for p in body_get.get("parameter", []) if p.get("name") == "result"), None
    )
    result_post = next(
        (p.get("valueBoolean") for p in body_post.get("parameter", []) if p.get("name") == "result"), None
    )
    assert result_get is False and result_post is False, (
        f"GET/POST no-match result divergence: GET={result_get!r}, "
        f"POST={result_post!r}; both expected False."
    )


# ===========================================================================
# Lens 7: Batch dispatcher parity (CS-03 HISTORIAN QA-052 methodology)
# ===========================================================================


def test_h70_batch_translate_route_exists_and_emits_fhir_json(fhir_client):
    """Batch dispatcher parity — $translate MUST be in the batch path-table.

    The batch dispatcher at lines 1151-1160 of apps/fhir_api.py MUST
    dispatch /ConceptMap/$translate via ``_extract_translate_params`` +
    ``_do_translate``. Without this, batch $translate would produce a
    per-entry 500 OperationOutcome (defeating per-entry isolation per
    FHIR R4 §3.7).
    """
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {
                    "method": "GET",
                    "url": "/ConceptMap/$translate?system="
                    f"{SNOMED_URI}&code=44054006&targetsystem={ICD10CM_URI}",
                }
            }
        ],
    }
    r = fhir_client.post("/fhir", json=bundle)
    assert r.status_code == 200, (
        f"Batch POST /fhir with $translate entry — expected 200; got "
        f"{r.status_code}: {r.text}"
    )
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"Content-Type drift on batch response: {r.headers['content-type']!r}"
    )
    body = r.json()
    assert body.get("resourceType") == "Bundle", (
        f"Batch response resourceType drift: {body.get('resourceType')!r}"
    )
    assert body.get("type") == "batch-response", (
        f"Batch response type drift: {body.get('type')!r}"
    )
    entries = body.get("entry", [])
    assert len(entries) == 1, f"Expected 1 batch entry; got {len(entries)}"
    entry_response = entries[0].get("response", {})
    assert entry_response.get("status", "").startswith("200"), (
        f"Batch entry status drift: {entry_response.get('status')!r}"
    )


def test_h71_batch_translate_clinical_content_matches_single_entry(fhir_client):
    """Batch dispatcher parity — single-vs-batch byte-exact.

    TS-04 TERMINOLOGIST methodology: the batch dispatcher reuses the
    same ``_do_*`` handlers and ``build_parameters_*`` builders as
    single-entry routes. Clinical content (target code, equivalence
    value) is structurally guaranteed identical.
    """
    # Single-entry
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
    single_matches = [
        p for p in single_body.get("parameter", []) if p.get("name") == "match"
    ]

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
    batch_entry_resource = batch_body["entry"][0].get("resource", {})
    batch_matches = [
        p for p in batch_entry_resource.get("parameter", []) if p.get("name") == "match"
    ]

    assert len(single_matches) == len(batch_matches), (
        f"Single-vs-batch match count divergence: single={len(single_matches)}, "
        f"batch={len(batch_matches)}."
    )
    for s, b in zip(single_matches, batch_matches):
        s_equiv = next(
            (p.get("valueCode") for p in s.get("part", []) if p.get("name") == "equivalence"), None
        )
        b_equiv = next(
            (p.get("valueCode") for p in b.get("part", []) if p.get("name") == "equivalence"), None
        )
        assert s_equiv == b_equiv, (
            f"Single-vs-batch equivalence divergence: single={s_equiv!r}, "
            f"batch={b_equiv!r}."
        )


# ===========================================================================
# Lens 8: Instance-level route exists (TS-02 SKEPTIC QA-014 pattern class)
# ===========================================================================


def test_h80_instance_level_translate_get_returns_fhir_response(fhir_client):
    """Instance-level GET /fhir/ConceptMap/{id}/$translate.

    The instance-level route MUST be registered (TS-02 SKEPTIC QA-014
    pattern class). Without it, requests fall through to the catch-all
    which returns a generic 404. The instance route returns a 404
    OperationOutcome with explanatory message (medterm4ds does not
    persist ConceptMaps).
    """
    r = fhir_client.get(
        "/fhir/ConceptMap/any-id/$translate",
        params=[
            ("system", SNOMED_URI),
            ("code", "44054006"),
            ("targetsystem", ICD10CM_URI),
        ],
    )
    # Instance-level route returns 404 OperationOutcome (ConceptMap
    # not persisted) — conformant per TS-02 SKEPTIC QA-014 pattern.
    assert r.status_code == 404, (
        f"Instance-level GET $translate — expected 404 (no persisted "
        f"ConceptMap); got {r.status_code}: {r.text}"
    )
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"Content-Type drift on instance-level route: "
        f"{r.headers['content-type']!r}"
    )
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome", (
        f"Instance-level route resourceType drift: {body.get('resourceType')!r}"
    )


def test_h81_instance_level_translate_post_returns_fhir_response(fhir_client):
    """Instance-level POST /fhir/ConceptMap/{id}/$translate.

    Sibling of test_h80 — POST route must also be registered.
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
    assert r.status_code == 404, (
        f"Instance-level POST $translate — expected 404; got "
        f"{r.status_code}: {r.text}"
    )
    assert r.headers["content-type"].startswith("application/fhir+json")
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


# ===========================================================================
# Lens 9: Builder-level direct unit tests (build_parameters_translate)
# ===========================================================================


def test_h90_builder_with_no_mappings_emits_result_false():
    """Builder-level direct test — ``build_parameters_translate`` with
    empty mappings.

    Per FHIR R4 $translate Out Parameters: ``result`` 1..1 boolean.
    Empty mappings → result=false (item 5 of chunk scope).
    """
    body = build_parameters_translate(
        [], source_system_uri=SNOMED_URI, source_code="44054006"
    )
    result_param = _find_param(body, "result")
    assert result_param is not None
    assert result_param.get("valueBoolean") is False


def test_h91_builder_with_known_match_emits_result_true_and_match_entry():
    """Builder-level direct test — ``build_parameters_translate`` with
    one known mapping.

    Verifies the match entry has the required parts (equivalence,
    concept, source) per item 4 of chunk scope.

    Note: CodeMapping requires ``source`` (CodeRef), ``target`` (CodeRef),
    ``relationship`` (str), ``match_type`` (str) per core/models.py:160.
    """
    from medterm4ds.core.models import CodeMapping, CodeRef

    source_ref = CodeRef(source="SNOMEDCT_US", code="44054006")
    target_ref = CodeRef(source="ICD10CM", code="E11")
    mapping = CodeMapping(
        source=source_ref,
        target=target_ref,
        relationship="equivalent",
        match_type="same_cui",
        target_display="Type 2 diabetes mellitus",
    )
    body = build_parameters_translate(
        [mapping], source_system_uri=SNOMED_URI, source_code="44054006"
    )
    result_param = _find_param(body, "result")
    assert result_param is not None
    assert result_param.get("valueBoolean") is True

    match_params = [p for p in body.get("parameter", []) if p.get("name") == "match"]
    assert len(match_params) == 1
    parts = match_params[0].get("part", [])
    part_names = {p.get("name") for p in parts}
    assert {"equivalence", "concept", "source"} <= part_names, (
        f"match entry missing required parts; got {part_names}"
    )

    equiv_part = next((p for p in parts if p.get("name") == "equivalence"), None)
    assert equiv_part is not None
    assert equiv_part.get("valueCode") == "equivalent"


def test_h92_builder_equivalence_value_in_r4_enum():
    """Builder-level closed-enum audit — every equivalence value emitted
    by ``build_parameters_translate`` MUST be in the R4 enum.

    The builder delegates to ``_fhir_equivalence_from_relationship``
    which delegates to the canonical module. The closed-enum membership
    is enforced at module load AND at builder output.

    Note: CodeMapping requires ``source`` (CodeRef), ``target`` (CodeRef),
    ``relationship`` (str), ``match_type`` (str) per core/models.py:160.
    """
    from medterm4ds.core.models import CodeMapping, CodeRef

    source_ref = CodeRef(source="SNOMEDCT_US", code="44054006")
    target_ref = CodeRef(source="ICD10CM", code="E11")
    # Exercise every engine token through the builder
    for rel in (
        "equivalent",
        "source-is-narrower-than-target",
        "source-is-broader-than-target",
        "related-to",
        "not-translated",
        "unmatched",
    ):
        mapping = CodeMapping(
            source=source_ref,
            target=target_ref,
            relationship=rel,
            match_type="same_cui",
            target_display="Type 2 diabetes mellitus",
        )
        body = build_parameters_translate(
            [mapping], source_system_uri=SNOMED_URI, source_code="44054006"
        )
        match_params = [p for p in body.get("parameter", []) if p.get("name") == "match"]
        for m in match_params:
            equiv = next(
                (p.get("valueCode") for p in m.get("part", []) if p.get("name") == "equivalence"),
                None,
            )
            assert equiv in FHIR_R4_CONCEPT_MAP_EQUIVALENCE, (
                f"Builder emitted equivalence {equiv!r} for relationship "
                f"{rel!r} — NOT in R4 closed enum. CF-HISTORIAN-VS01-01 "
                f"regression at the builder level."
            )


# ===========================================================================
# Lens 10: Content-Type / wire-format audit on $translate route
# CR-001 / CR-002 (XML boolean capitalization) — verify $translate
# operation route emits FHIR-conformant Content-Type on every shape.
# ===========================================================================


def test_h100_translate_get_emits_fhir_json_content_type(fhir_client):
    """Content-Type audit — GET $translate MUST emit application/fhir+json.

    Per FHIR R4 §3.1.0.1.9 + CR-001 (milestone-1 code review): every
    operation handler MUST funnel through ``_fhir_response`` so the
    Content-Type is set explicitly (not FastAPI's default
    ``application/json``).
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
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"GET $translate Content-Type drift: {r.headers['content-type']!r}; "
        f"expected application/fhir+json. CR-001 regression."
    )


def test_h101_translate_post_emits_fhir_json_content_type(fhir_client):
    """Content-Type audit — POST $translate MUST emit application/fhir+json.

    Sibling of test_h100 on the POST path.
    """
    r = fhir_client.post(
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
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+json"), (
        f"POST $translate Content-Type drift: {r.headers['content-type']!r}. "
        f"CR-001 regression."
    )


def test_h102_translate_get_emits_fhir_xml_when_requested(fhir_client):
    """XML wire-format audit — GET $translate with _format=xml MUST emit
    application/fhir+xml.

    CR-002 (milestone-1 code review): the XML serializer's
    ``_scalar_to_xml_attr`` boolean special-case MUST apply on the
    $translate route. ``result`` is a boolean — verify the wire form
    uses lowercase ``true``/``false`` (NOT Python's ``str(True)`` =
    ``'True'``).
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
    assert r.status_code == 200, (
        f"GET $translate _format=xml — expected 200; got {r.status_code}: {r.text}"
    )
    assert r.headers["content-type"].startswith("application/fhir+xml"), (
        f"GET $translate _format=xml Content-Type drift: "
        f"{r.headers['content-type']!r}; expected application/fhir+xml."
    )
    body_text = r.text
    # The Parameters body has a ``<valueBoolean value="..."/>`` for the
    # ``result`` parameter. Verify lowercase form per CR-002.
    assert 'value="true"' in body_text or 'value="false"' in body_text, (
        f"XML wire-format drift: result boolean not rendered in lowercase "
        f"per CR-002. Body: {body_text[:500]}"
    )
    # MUST NOT contain capital-T form (Python's str(True))
    assert 'value="True"' not in body_text, (
        f"XML wire-format drift: capital-T 'True' rendered per CR-002 "
        f"regression. Body: {body_text[:500]}"
    )
    assert 'value="False"' not in body_text, (
        f"XML wire-format drift: capital-F 'False' rendered per CR-002 "
        f"regression. Body: {body_text[:500]}"
    )
