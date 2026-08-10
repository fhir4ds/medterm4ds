"""VS-05 TERMINOLOGIST resweep: ValueSet $validate-code Operation.

Source: https://build.fhir.org/valueset-operation-validate-code.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-validate-code.html

This is the resweep (4th and final personality) pass for chunk VS-05.

TERMINOLOGIST lens: clinical and terminological correctness. The other
personalities find technical bugs; TERMINOLOGIST finds domain bugs.

Per GLOBAL_RULES.md, TERMINOLOGIST findings are HIGH severity by default.

EXPLORER tip for TERMINOLOGIST (5 hand-off items, most load-bearing first):

  Tip #1: Canonical-DISPLAY META-PATTERN clinical-correctness dimension
    on cc matched coding (especially when mixed-system cc codings have
    varying specificity). Verify that the Out display surfaced for a
    codeableConcept matched coding is CLINICALLY correct (not just
    structurally correct) — i.e. it matches the engine canonical STR
    for the matched code's SAB.

  Tip #2: Cross-handler CS↔VS message-parity META-PATTERN clinical-
    informativeness dimension on unknown-code path. Verify the message
    cites code AND canonical system URI — clinically useful for CDS
    hooks.

  Tip #3: CS-03 SKEPTIC AUDIT-002 status intact — evaluate clinical-
    safety implications of silent result=true on cc with WRONG display
    on matched coding (consider future-enhancement chunk surfacing
    clinical-safety warning even when result=true).

  Tip #4: Lateral-combination clinical-content parity on batch surface.
    Verify batch entries with semantically-overlapping matched codes
    produce clinically-consistent results (e.g. SNOMED T2DM and
    ICD-10-CM E11 both refer to the same clinical concept — verify
    their canonical displays align on the batch surface).

  Tip #5: Implicit VS URL with display mismatch — verify message is
    clinically sensible regardless of URL form (explicit vs implicit
    VS URL form both produce clinically informative messages).

Additional TERMINOLOGIST probe classes:

  - Source-read structural contracts for clinical-correctness invariants
    (matched coding canonical uri, matched coding display, batch
    dispatcher clinical-content parity).
  - Clinical-content byte-exact parity between single-entry and batch
    invocations on semantically-overlapping codes.
  - Cross-resource clinical consistency: VS-$validate-code Out display
    matches $lookup Out display for every seeded code.

Conformance fixture (per conftest.py): 4 mrconso rows + 1 mrrel row.
  SNOMED 73211009 = "Diabetes mellitus"        (broader concept)
  SNOMED 44054006 = "Type 2 diabetes mellitus" (narrower concept)
  ICD-10-CM E11   = "Type 2 diabetes mellitus" (same concept as SNOMED T2DM)
  RxNorm  860975  = "24 HR metformin 500 MG Oral Tablet" (treatment)
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

# Spec:
#   https://hl7.org/fhir/R4/valueset-operation-validate-code.html
#   https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI, canonical_system_uri

# ---------------------------------------------------------------------------
# Constants — seeded systems + codes (mirror SKEPTIC + HISTORIAN + EXPLORER resweep).
# ---------------------------------------------------------------------------

SNOMED_URI = SYSTEM_TO_FHIR_URI["SNOMEDCT_US"]    # http://snomed.info/sct
ICD10CM_URI = SYSTEM_TO_FHIR_URI["ICD10CM"]       # http://hl7.org/fhir/sid/icd-10-cm
RXNORM_URI = SYSTEM_TO_FHIR_URI["RXNORM"]         # http://www.nlm.nih.gov/research/umls/rxnorm

SNOMED_DM_CODE = "73211009"
SNOMED_DM_DISPLAY = "Diabetes mellitus"
SNOMED_T2DM_CODE = "44054006"
SNOMED_T2DM_DISPLAY = "Type 2 diabetes mellitus"
ICD10CM_E11_CODE = "E11"
ICD10CM_E11_DISPLAY = "Type 2 diabetes mellitus"
RXNORM_METFORMIN_CODE = "860975"
RXNORM_METFORMIN_DISPLAY = "24 HR metformin 500 MG Oral Tablet"

# SNOMED-CT US edition URI (per SYSTEM_TO_FHIR_URI registry canonical form)
SNOMED_OID_ALIAS = "urn:oid:2.16.840.1.113883.6.96"

# Path to apps/fhir_api.py for source-read structural probes.
_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)


# =============================================================================
# Helpers
# =============================================================================


def _param_value(body: dict, name: str):
    """Return the first value* field for an Out parameter named ``name``."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


def _has_param(body: dict, name: str) -> bool:
    return any(p.get("name") == name for p in body.get("parameter", []))


def _lookup_out_display(client, system: str, code: str):
    """Return canonical display for ``code`` from $lookup."""
    r = client.get(f"/fhir/CodeSystem/$lookup?system={system}&code={code}")
    if r.status_code != 200:
        return None
    return _param_value(r.json(), "display")


def _validate_vs_get(client, *, system=None, code=None, display=None, url=None):
    params = []
    if system is not None:
        params.append(("system", system))
    if code is not None:
        params.append(("code", code))
    if display is not None:
        params.append(("display", display))
    if url is not None:
        params.append(("url", url))
    return client.get("/fhir/ValueSet/$validate-code", params=params)


def _validate_vs_post(client, body: dict):
    """POST /fhir/ValueSet/$validate-code with a Parameters body."""
    return client.post(
        "/fhir/ValueSet/$validate-code",
        json=body,
        headers={"Accept": "application/fhir+json"},
    )


def _validate_cs_get(client, *, system=None, code=None, display=None):
    params = []
    if system is not None:
        params.append(("system", system))
    if code is not None:
        params.append(("code", code))
    if display is not None:
        params.append(("display", display))
    return client.get("/fhir/CodeSystem/$validate-code", params=params)


def _parameters_body(*pairs: tuple[str, dict]) -> dict:
    """Build a Parameters body from (name, value*) tuples."""
    return {
        "resourceType": "Parameters",
        "parameter": [{"name": n, **rest} for (n, rest) in pairs],
    }


def _scalar_post_body(system: str, code: str, display: str | None = None) -> dict:
    pairs: list[tuple[str, dict]] = [
        ("system", {"valueUri": system}),
        ("code", {"valueCode": code}),
    ]
    if display is not None:
        pairs.append(("display", {"valueString": display}))
    return _parameters_body(*pairs)


def _codeable_concept_post_body(
    codings: list[tuple[str, str]],
    display: str | None = None,
) -> dict:
    """Build a Parameters body with a valueCodeableConcept."""
    cc_coding = [
        {"system": s, "code": c} for (s, c) in codings
    ]
    pairs: list[tuple[str, dict]] = [
        (
            "codeableConcept",
            {"valueCodeableConcept": {"coding": cc_coding}},
        ),
    ]
    if display is not None:
        pairs.append(("display", {"valueString": display}))
    return _parameters_body(*pairs)


def _batch_bundle(entries: list[dict]) -> dict:
    """Build a Bundle type=batch with the given entries."""
    return {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": entries,
    }


def _batch_post_entry(url: str, body: dict) -> dict:
    """Build a POST batch entry."""
    return {
        "request": {"method": "POST", "url": url},
        "resource": body,
    }


def _read_module_source() -> str:
    return inspect.getsource(
        __import__("medterm4ds.apps.fhir_api", fromlist=["fhir_api"])
    )


def _read_nested_function_source(module_src: str, parent_name: str, child_name: str):
    """Return source of a nested function defined inside ``parent_name``.

    Walks BOTH ast.FunctionDef AND ast.AsyncFunctionDef inside ``parent``
    (mirrors CS-03 HISTORIAN + VS-04 HISTORIAN + VS-05 SKEPTIC/EXPLORER helper).
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
# Lens 1: Canonical-DISPLAY META-PATTERN clinical-correctness dimension
# on codeableConcept matched coding (EXPLORER tip #1)
# =============================================================================


class TestLens1CanonicalDisplayCcMatchedCoding:
    """Verify cc matched coding Out display is CLINICALLY correct.

    The cc branch of ``_do_vs_validate`` iterates codings and returns the
    FIRST match's canonical display. TERMINOLOGIST verifies the surfaced
    Out display matches the engine canonical STR for the matched code's
    SAB — not just structurally correct, but clinically correct.
    """

    def test_t10_single_coding_cc_out_display_is_engine_canonical_str(
        self, fhir_client,
    ):
        """cc with single SNOMED T2DM coding → Out display = engine canonical STR."""
        body = _codeable_concept_post_body([(SNOMED_URI, SNOMED_T2DM_CODE)])
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        out_display = _param_value(r.json(), "display")
        assert out_display == SNOMED_T2DM_DISPLAY, (
            f"Out display should be engine canonical STR "
            f"({SNOMED_T2DM_DISPLAY!r}); got {out_display!r}"
        )

    @pytest.mark.parametrize(
        "system, code, expected_display",
        [
            (SNOMED_URI, SNOMED_DM_CODE, SNOMED_DM_DISPLAY),
            (SNOMED_URI, SNOMED_T2DM_CODE, SNOMED_T2DM_DISPLAY),
            (ICD10CM_URI, ICD10CM_E11_CODE, ICD10CM_E11_DISPLAY),
            (RXNORM_URI, RXNORM_METFORMIN_CODE, RXNORM_METFORMIN_DISPLAY),
        ],
    )
    def test_t11_cc_matched_coding_display_matches_lookup_per_system(
        self, fhir_client, system, code, expected_display,
    ):
        """cc matched coding Out display byte-exact equals $lookup Out display.

        Per EXPLORER tip #1: the Out display for a cc matched coding is the
        CLINICALLY correct engine canonical preferred term for that SAB.
        The engine canonical STR for SNOMED DM is "Diabetes mellitus" (the
        broader clinical concept); for SNOMED T2DM is "Type 2 diabetes
        mellitus" (the narrower clinical concept). A client CDS hook reading
        this response would interpret the concept at the right clinical
        granularity.
        """
        body = _codeable_concept_post_body([(system, code)])
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        cc_display = _param_value(r.json(), "display")
        lookup_display = _lookup_out_display(fhir_client, system, code)
        assert cc_display == lookup_display == expected_display, (
            f"cc matched coding display {cc_display!r} should byte-exact "
            f"equal $lookup display {lookup_display!r} AND the engine "
            f"canonical STR {expected_display!r}"
        )

    def test_t12_cc_mixed_specificity_codings_first_match_is_clinically_correct(
        self, fhir_client,
    ):
        """cc with mixed-specificity codings [DM, T2DM] → first match (DM)
        Out display is "Diabetes mellitus" (the broader clinical concept).

        Clinical scenario: a client supplies both the SNOMED parent (DM,
        73211009) and child (T2DM, 44054006) codings in a codeableConcept.
        First-match-wins iterates DM first → Out display = "Diabetes
        mellitus". This is CLINICALLY correct because the matched code IS
        DM, not T2DM — the broader clinical concept.
        """
        body = _codeable_concept_post_body([
            (SNOMED_URI, SNOMED_DM_CODE),
            (SNOMED_URI, SNOMED_T2DM_CODE),
        ])
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        out_display = _param_value(r.json(), "display")
        assert out_display == SNOMED_DM_DISPLAY, (
            f"cc first-match Out display should be DM canonical "
            f"({SNOMED_DM_DISPLAY!r}); got {out_display!r}. "
            f"Per spec, the server surfaces the matched coding's display — "
            f"matched code IS DM, NOT T2DM."
        )

    def test_t13_cc_mixed_specificity_reverse_order_clinical_correctness(
        self, fhir_client,
    ):
        """cc with reverse order [T2DM, DM] → first match (T2DM)
        Out display is "Type 2 diabetes mellitus" (the narrower concept).

        Clinical scenario: a client supplies [child, parent] codings.
        First-match-wins iterates T2DM first → Out display = T2DM canonical.
        This is CLINICALLY correct because the matched code IS T2DM, the
        narrower concept.
        """
        body = _codeable_concept_post_body([
            (SNOMED_URI, SNOMED_T2DM_CODE),
            (SNOMED_URI, SNOMED_DM_CODE),
        ])
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        out_display = _param_value(r.json(), "display")
        assert out_display == SNOMED_T2DM_DISPLAY, (
            f"cc first-match Out display should be T2DM canonical "
            f"({SNOMED_T2DM_DISPLAY!r}); got {out_display!r}. "
            f"Per spec, the server surfaces the matched coding's display — "
            f"matched code IS T2DM, NOT DM."
        )

    def test_t14_cc_mixed_systems_first_match_in_clinically_correct_system(
        self, fhir_client,
    ):
        """cc with mixed-system codings [SNOMED-T2DM, ICD10CM-E11] → first
        match surfaces SNOMED canonical. Clinical correctness: client
        supplied two codings referring to the SAME clinical concept (Type 2
        diabetes mellitus) across two code systems. The matched coding is
        SNOMED T2DM, so Out display MUST be SNOMED T2DM's canonical STR.
        """
        body = _codeable_concept_post_body([
            (SNOMED_URI, SNOMED_T2DM_CODE),
            (ICD10CM_URI, ICD10CM_E11_CODE),
        ])
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        out_display = _param_value(r.json(), "display")
        out_system = _param_value(r.json(), "system")
        assert out_display == SNOMED_T2DM_DISPLAY, (
            f"cc first-match Out display should be SNOMED T2DM canonical "
            f"({SNOMED_T2DM_DISPLAY!r}); got {out_display!r}"
        )
        assert out_system == SNOMED_URI, (
            f"cc first-match Out system should be SNOMED canonical URI "
            f"({SNOMED_URI!r}); got {out_system!r}"
        )

    def test_t15_cc_mixed_systems_reverse_order_clinical_correctness(
        self, fhir_client,
    ):
        """Reverse: [ICD10CM-E11, SNOMED-T2DM] → first match is ICD-10-CM
        E11. Out display = ICD-10-CM canonical ("Type 2 diabetes mellitus")
        — the same clinical concept in a different code system.
        """
        body = _codeable_concept_post_body([
            (ICD10CM_URI, ICD10CM_E11_CODE),
            (SNOMED_URI, SNOMED_T2DM_CODE),
        ])
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        out_display = _param_value(r.json(), "display")
        out_system = _param_value(r.json(), "system")
        assert out_display == ICD10CM_E11_DISPLAY, (
            f"cc first-match Out display should be ICD-10-CM E11 canonical "
            f"({ICD10CM_E11_DISPLAY!r}); got {out_display!r}"
        )
        assert out_system == ICD10CM_URI, (
            f"cc first-match Out system should be ICD-10-CM canonical URI "
            f"({ICD10CM_URI!r}); got {out_system!r}"
        )


# =============================================================================
# Lens 2: Cross-handler CS↔VS message-parity clinical-informativeness
# dimension on unknown-code path (EXPLORER tip #2)
# =============================================================================


class TestLens2CrossHandlerMessageInformativeness:
    """Verify unknown-code message is clinically informative for CDS hooks.

    Per spec Out `message`: "Error details, if result = false". TERMINOLOGIST
    verifies the message text is CLINICALLY informative — it cites BOTH the
    code AND the canonical system URI. A CDS hook reading this message can
    surface actionable context to the clinician ("Code X is not valid in
    code system Y").
    """

    @pytest.mark.parametrize(
        "system, code",
        [
            (SNOMED_URI, "9999999999"),       # unknown SNOMED code
            (ICD10CM_URI, "Z99.99"),          # unknown ICD-10-CM code
            (RXNORM_URI, "9999999"),          # unknown RxNorm code
        ],
    )
    def test_t20_unknown_code_message_cites_code_and_canonical_system_uri(
        self, fhir_client, system, code,
    ):
        """Unknown-code message MUST cite BOTH the code AND the canonical
        system URI. Clinically informative for CDS hooks.
        """
        r = _validate_vs_get(
            fhir_client, system=system, code=code,
        )
        assert r.status_code == 200
        body = r.json()
        assert _param_value(body, "result") is False
        msg = _param_value(body, "message")
        assert msg is not None, (
            f"Unknown-code response MUST carry a message (spec Out `message`: "
            f"'Error details, if result = false')"
        )
        # Clinical-informativeness assertions: message cites BOTH code AND system.
        assert code in msg, (
            f"Message should cite the unknown code {code!r}; got {msg!r}"
        )
        assert system in msg, (
            f"Message should cite the canonical system URI {system!r}; "
            f"got {msg!r}. CDS hooks reading this message need the system "
            f"to surface actionable clinical context."
        )

    def test_t21_unknown_code_message_clinically_informative_via_oid_alias(
        self, fhir_client,
    ):
        """Unknown-code message cites the CANONICAL URI when client used
        an OID alias input. The OID alias resolves through the canonical
        registry (CR-011/CR-025) — the message surfaces the canonical
        URI, not the client alias. This is clinically correct because CDS
        hooks expect the canonical URI in error messages.
        """
        r = _validate_vs_get(
            fhir_client,
            system=SNOMED_OID_ALIAS,
            code="9999999999",
        )
        assert r.status_code == 200
        body = r.json()
        assert _param_value(body, "result") is False
        msg = _param_value(body, "message")
        assert msg is not None
        # Message MUST cite the canonical URI (SNOMED), not the OID alias.
        assert SNOMED_URI in msg, (
            f"Unknown-code message should cite canonical SNOMED URI "
            f"({SNOMED_URI!r}), not the OID alias input; got {msg!r}"
        )
        assert SNOMED_OID_ALIAS not in msg, (
            f"Unknown-code message should NOT cite the client-supplied OID "
            f"alias input; got {msg!r}. Clinical correctness: clients "
            f"expect canonical URIs in error messages."
        )

    @pytest.mark.parametrize(
        "system, code",
        [
            (SNOMED_URI, "9999999999"),
            (ICD10CM_URI, "Z99.99"),
            (RXNORM_URI, "9999999"),
        ],
    )
    def test_t22_cs_vs_message_byte_exact_parity_on_unknown_code(
        self, fhir_client, system, code,
    ):
        """CS↔VS message-parity on unknown-code path. Both handlers emit
        byte-exact messages. The CS handler message format is the load-
        bearing contract; VS mirrors it.
        """
        cs_r = _validate_cs_get(fhir_client, system=system, code=code)
        vs_r = _validate_vs_get(fhir_client, system=system, code=code)
        assert cs_r.status_code == 200
        assert vs_r.status_code == 200
        cs_msg = _param_value(cs_r.json(), "message")
        vs_msg = _param_value(vs_r.json(), "message")
        assert cs_msg == vs_msg, (
            f"CS↔VS unknown-code message byte-exact parity: CS={cs_msg!r}, "
            f"VS={vs_msg!r}. The sibling handlers MUST emit byte-exact "
            f"messages on every message-format class."
        )

    def test_t23_unknown_code_message_no_engine_internal_leakage(
        self, fhir_client,
    ):
        """Unknown-code message does NOT leak engine-internal vocabulary
        (e.g. SAB labels like 'SNOMEDCT_US', CUI codes, AUI codes).
        Clinical correctness: the message uses the FHIR canonical URI +
        code, not engine internals.
        """
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code="9999999999",
        )
        msg = _param_value(r.json(), "message")
        assert msg is not None
        # Engine internals that MUST NOT leak
        assert "SNOMEDCT_US" not in msg, (
            f"Message must not leak SAB label; got {msg!r}"
        )
        assert "AUI" not in msg.upper(), (
            f"Message must not leak AUI vocabulary; got {msg!r}"
        )
        assert "CUI" not in msg.upper(), (
            f"Message must not leak CUI vocabulary; got {msg!r}"
        )


# =============================================================================
# Lens 3: CS-03 SKEPTIC AUDIT-002 status intact — clinical-safety
# implications (EXPLORER tip #3)
# =============================================================================


class TestLens3Audit002ClinicalSafetyImplications:
    """Evaluate clinical-safety implications of silent result=true on cc
    with WRONG display on matched coding.

    Per CS-03 SKEPTIC AUDIT-002 (spec-permitted non-enforcement), the
    codeableConcept path does NOT trigger display mismatch enforcement.
    TERMINOLOGIST verifies the current semantic AND documents the
    clinical-safety implications via carry-forward-as-probe pattern.
    """

    def test_t30_cc_wrong_display_on_matched_coding_returns_result_true(
        self, fhir_client,
    ):
        """cc with WRONG display on matched coding → result=true. Per
        CS-03 SKEPTIC AUDIT-002, this is spec-permitted non-enforcement.

        Clinical-safety IMPLICATION: a client supplying a codeableConcept
        with a wrong display on the matched coding receives result=true
        silently — no warning surfaced. The Out display field surfaces the
        canonical (matched) display, so clients that read both fields have
        the correct value, but clients that only read result lose the signal.
        Pinned via carry-forward-as-probe pattern.
        """
        body = _codeable_concept_post_body(
            [(SNOMED_URI, SNOMED_T2DM_CODE)],
            display="WRONG DISPLAY NOT MATCHING ENGINE CANONICAL",
        )
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        body_json = r.json()
        # result=true per spec-permitted non-enforcement
        assert _param_value(body_json, "result") is True
        # Out display surfaces the canonical (matched) display
        out_display = _param_value(body_json, "display")
        assert out_display == SNOMED_T2DM_DISPLAY, (
            f"Out display should surface matched coding canonical "
            f"({SNOMED_T2DM_DISPLAY!r}); got {out_display!r}"
        )
        # No message field — silent result=true (clinical-safety implication
        # pinned via this carry-forward-as-probe; future enhancement may
        # surface a clinical-safety warning even when result=true).

    def test_t31_cc_wrong_display_scalar_path_enforces_mismatch_asymmetry(
        self, fhir_client,
    ):
        """Scalar path with WRONG display → result=false. The asymmetry
        between scalar (enforces) and cc (does not enforce) is the
        load-bearing clinical-safety contract documented via
        carry-forward-as-probe pattern.
        """
        r = _validate_vs_get(
            fhir_client,
            system=SNOMED_URI,
            code=SNOMED_T2DM_CODE,
            display="WRONG DISPLAY NOT MATCHING ENGINE CANONICAL",
        )
        assert r.status_code == 200
        body = r.json()
        assert _param_value(body, "result") is False
        msg = _param_value(body, "message")
        assert msg is not None
        assert "WRONG DISPLAY" in msg, (
            f"Scalar path mismatch message should cite wrong display; got {msg!r}"
        )

    def test_t32_cc_audit_002_status_source_read_contract(
        self,
    ):
        """Source-read contract: ``_do_vs_validate`` cc branch ABSENT of
        display-mismatch check while scalar branch PRESENT.

        This is the load-bearing structural contract for the CS-03 SKEPTIC
        AUDIT-002 status: spec-permitted non-enforcement on the cc path.
        The contract SURVIVES refactors because it walks the AST tree of
        the specific branch (not substring match on whole function).
        """
        module_src = _read_module_source()
        vs_func = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_vs_validate",
        )
        assert vs_func is not None, (
            "_do_vs_validate function not found inside create_fhir_app"
        )
        tree = ast.parse(vs_func)
        # Find the `if codeable_concept_pairs:` block
        cc_branch_src = None
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                # Look for the if-block testing codeable_concept_pairs
                test_src = ast.get_source_segment(vs_func, node)
                if (
                    test_src is not None
                    and "codeable_concept_pairs" in test_src
                    and "if matched_info is not None" in test_src
                    and "build_parameters_validate(\n                    True," in test_src
                ):
                    cc_branch_src = test_src
                    break
        assert cc_branch_src is not None, (
            "cc branch not found in _do_vs_validate"
        )
        # CRITICAL clinical-safety invariant: the cc branch must NOT
        # contain a display-mismatch check
        assert (
            "display != canonical_display" not in cc_branch_src
            and "display != " not in cc_branch_src
        ), (
            "cc branch MUST NOT contain display-mismatch check "
            "(CS-03 SKEPTIC AUDIT-002). "
            "If a future enhancement surfaces clinical-safety warnings "
            "on cc-with-wrong-display, this contract MUST be updated."
        )

    def test_t33_cc_audit_002_scalar_branch_does_enforce_contract(
        self,
    ):
        """Mirror of test_t32 — scalar branch DOES enforce display
        mismatch. Pinned via the same source-read contract approach.
        """
        module_src = _read_module_source()
        vs_func = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_vs_validate",
        )
        assert vs_func is not None
        # The scalar branch (after the cc branch) MUST contain the check.
        # Look for "display != canonical_display" anywhere in the function.
        assert (
            "display != canonical_display" in vs_func
        ), (
            "Scalar branch of _do_vs_validate MUST contain "
            "'display != canonical_display' check (mirror of CS-03 SKEPTIC "
            "QA-048 on the sibling CodeSystem handler)."
        )


# =============================================================================
# Lens 4: Lateral-combination clinical-content parity on batch surface
# (EXPLORER tip #4)
# =============================================================================


class TestLens4BatchClinicalContentParity:
    """Verify batch entries with semantically-overlapping matched codes
    produce clinically-consistent results.

    Clinical scenario: a client sends a batch with two entries —
    entry 1 validates SNOMED T2DM (44054006); entry 2 validates ICD-10-CM
    E11 (E11). Both refer to the SAME clinical concept (Type 2 diabetes
    mellitus). TERMINOLOGIST verifies that their canonical displays align
    on the batch surface (both surface "Type 2 diabetes mellitus").
    """

    def test_t40_batch_semantically_overlapping_codes_produce_consistent_displays(
        self, fhir_client,
    ):
        """Batch with [SNOMED-T2DM, ICD10CM-E11] → both entries surface
        "Type 2 diabetes mellitus" canonical display. Clinically
        consistent — same concept across two code systems.
        """
        batch = _batch_bundle([
            _batch_post_entry(
                "/fhir/ValueSet/$validate-code",
                _scalar_post_body(SNOMED_URI, SNOMED_T2DM_CODE),
            ),
            _batch_post_entry(
                "/fhir/ValueSet/$validate-code",
                _scalar_post_body(ICD10CM_URI, ICD10CM_E11_CODE),
            ),
        ])
        r = fhir_client.post("/fhir", json=batch)
        assert r.status_code == 200
        body = r.json()
        assert body["resourceType"] == "Bundle"
        assert body["type"] == "batch-response"
        assert len(body["entry"]) == 2
        entry1_display = _param_value(
            body["entry"][0]["resource"], "display",
        )
        entry2_display = _param_value(
            body["entry"][1]["resource"], "display",
        )
        # Both should surface "Type 2 diabetes mellitus"
        assert entry1_display == SNOMED_T2DM_DISPLAY, (
            f"Batch entry 1 display should be SNOMED T2DM canonical "
            f"({SNOMED_T2DM_DISPLAY!r}); got {entry1_display!r}"
        )
        assert entry2_display == ICD10CM_E11_DISPLAY, (
            f"Batch entry 2 display should be ICD-10-CM E11 canonical "
            f"({ICD10CM_E11_DISPLAY!r}); got {entry2_display!r}"
        )
        # Clinical consistency: both displays align (same concept)
        assert entry1_display == entry2_display, (
            f"Clinical-content parity: SNOMED T2DM and ICD-10-CM E11 refer "
            f"to the SAME clinical concept; displays should align. "
            f"entry1={entry1_display!r}, entry2={entry2_display!r}"
        )

    def test_t41_batch_mixed_scalar_cc_clinical_content_parity(
        self, fhir_client,
    ):
        """Batch with [scalar-SNOMED-T2DM, cc-[ICD10CM-E11]] → both
        entries surface "Type 2 diabetes mellitus". Clinical-content
        parity across scalar+cc encodings on the batch surface.
        """
        batch = _batch_bundle([
            _batch_post_entry(
                "/fhir/ValueSet/$validate-code",
                _scalar_post_body(SNOMED_URI, SNOMED_T2DM_CODE),
            ),
            _batch_post_entry(
                "/fhir/ValueSet/$validate-code",
                _codeable_concept_post_body([(ICD10CM_URI, ICD10CM_E11_CODE)]),
            ),
        ])
        r = fhir_client.post("/fhir", json=batch)
        assert r.status_code == 200
        body = r.json()
        assert body["resourceType"] == "Bundle"
        assert body["type"] == "batch-response"
        entry1_display = _param_value(
            body["entry"][0]["resource"], "display",
        )
        entry2_display = _param_value(
            body["entry"][1]["resource"], "display",
        )
        assert entry1_display == SNOMED_T2DM_DISPLAY
        assert entry2_display == ICD10CM_E11_DISPLAY
        # Clinical-content parity: scalar and cc encodings both surface
        # the SAME clinical concept's canonical display.
        assert entry1_display == entry2_display, (
            f"Clinical-content parity: scalar (SNOMED T2DM) and cc "
            f"(ICD-10-CM E11) refer to the SAME clinical concept; "
            f"displays should align. "
            f"entry1={entry1_display!r}, entry2={entry2_display!r}"
        )

    def test_t42_batch_single_vs_batch_byte_exact_clinical_content(
        self, fhir_client,
    ):
        """Single-entry invocation and batch invocation of the same
        (system, code) produce byte-exact Out parameters. Clinical
        content integrity is structural — the batch dispatcher must
        NEVER alter clinical content.
        """
        single_r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
        )
        batch = _batch_bundle([
            _batch_post_entry(
                "/fhir/ValueSet/$validate-code",
                _scalar_post_body(SNOMED_URI, SNOMED_T2DM_CODE),
            ),
        ])
        batch_r = fhir_client.post("/fhir", json=batch)
        assert single_r.status_code == 200
        assert batch_r.status_code == 200
        single_params = single_r.json().get("parameter", [])
        batch_params = batch_r.json()["entry"][0]["resource"].get("parameter", [])
        # Compare the result/display/system/code parameters (ignore message
        # which may differ slightly in batch isolation logs)
        for name in ("result", "display", "system", "code"):
            single_v = next(
                (p.get(f"value{'Boolean' if name == 'result' else 'String' if name == 'display' else 'Uri' if name == 'system' else 'Code'}")
                 for p in single_params if p.get("name") == name),
                None,
            )
            batch_v = next(
                (p.get(f"value{'Boolean' if name == 'result' else 'String' if name == 'display' else 'Uri' if name == 'system' else 'Code'}")
                 for p in batch_params if p.get("name") == name),
                None,
            )
            assert single_v == batch_v, (
                f"Clinical-content parity for {name!r}: single={single_v!r}, "
                f"batch={batch_v!r}"
            )

    def test_t43_batch_unknown_code_clinical_informativeness_preserved(
        self, fhir_client,
    ):
        """Batch with unknown code → per-entry message still clinically
        informative (cites code AND canonical system URI). Clinical
        safety on the batch surface.
        """
        batch = _batch_bundle([
            _batch_post_entry(
                "/fhir/ValueSet/$validate-code",
                _scalar_post_body(SNOMED_URI, "9999999999"),
            ),
        ])
        r = fhir_client.post("/fhir", json=batch)
        assert r.status_code == 200
        body = r.json()
        entry_resource = body["entry"][0]["resource"]
        msg = _param_value(entry_resource, "message")
        assert msg is not None
        assert "9999999999" in msg, (
            f"Batch unknown-code message should cite code; got {msg!r}"
        )
        assert SNOMED_URI in msg, (
            f"Batch unknown-code message should cite canonical system URI; "
            f"got {msg!r}"
        )


# =============================================================================
# Lens 5: Implicit VS URL with display mismatch clinical sensibility
# (EXPLORER tip #5)
# =============================================================================


class TestLens5ImplicitVsUrlDisplayMismatchSensibility:
    """Verify the display-mismatch message is clinically sensible
    regardless of URL form (explicit vs implicit VS URL).

    The implementation treats the implicit VS URL form (e.g.
    ``http://snomed.info/sct`` as the URL) as equivalent to the explicit
    system+code form. TERMINOLOGIST verifies that the message text is
    clinically sensible across both URL forms — the message cites the
    canonical system URI, not the raw implicit VS URL string.
    """

    def test_t50_implicit_vs_url_display_mismatch_message_is_clinically_sensible(
        self, fhir_client,
    ):
        """Implicit VS URL + display mismatch → message cites the wrong
        display verbatim. Clinically actionable for CDS hooks.
        """
        r = _validate_vs_get(
            fhir_client,
            url=SNOMED_URI,
            system=SNOMED_URI,
            code=SNOMED_T2DM_CODE,
            display="WRONG DISPLAY ON IMPLICIT VS URL",
        )
        assert r.status_code == 200
        body = r.json()
        assert _param_value(body, "result") is False
        msg = _param_value(body, "message")
        assert msg is not None
        # Message cites the wrong display verbatim
        assert "WRONG DISPLAY ON IMPLICIT VS URL" in msg, (
            f"Implicit VS URL mismatch message should cite wrong display "
            f"verbatim; got {msg!r}"
        )

    def test_t51_implicit_vs_url_message_byte_exact_parity_with_explicit(
        self, fhir_client,
    ):
        """Implicit VS URL + display mismatch message byte-exact equals
        explicit system+code form message. Clinical sensibility holds
        regardless of URL form.
        """
        implicit_r = _validate_vs_get(
            fhir_client,
            url=SNOMED_URI,
            system=SNOMED_URI,
            code=SNOMED_T2DM_CODE,
            display="WRONG DISPLAY",
        )
        explicit_r = _validate_vs_get(
            fhir_client,
            system=SNOMED_URI,
            code=SNOMED_T2DM_CODE,
            display="WRONG DISPLAY",
        )
        assert implicit_r.status_code == 200
        assert explicit_r.status_code == 200
        implicit_msg = _param_value(implicit_r.json(), "message")
        explicit_msg = _param_value(explicit_r.json(), "message")
        assert implicit_msg == explicit_msg, (
            f"Message byte-exact parity between implicit VS URL and "
            f"explicit system+code forms: implicit={implicit_msg!r}, "
            f"explicit={explicit_msg!r}"
        )

    def test_t52_implicit_vs_url_canonical_out_system_clinical_correctness(
        self, fhir_client,
    ):
        """Implicit VS URL + valid code → Out system is canonical SNOMED
        URI (not the raw implicit VS URL string). Clinical correctness:
        clients reading Out system expect the canonical FHIR URI.
        """
        r = _validate_vs_get(
            fhir_client,
            url=SNOMED_URI,
            system=SNOMED_URI,
            code=SNOMED_T2DM_CODE,
        )
        assert r.status_code == 200
        out_system = _param_value(r.json(), "system")
        assert out_system == SNOMED_URI, (
            f"Out system should be canonical SNOMED URI "
            f"({SNOMED_URI!r}); got {out_system!r}"
        )

    def test_t53_implicit_vs_url_oid_alias_resolves_to_canonical(
        self, fhir_client,
    ):
        """Implicit VS URL via OID alias + valid code → Out system is
        canonical SNOMED URI (CR-011/CR-025 client-input-as-canonical
        drift pattern). Clinical correctness on the alias-input path.
        """
        r = _validate_vs_get(
            fhir_client,
            url=SNOMED_OID_ALIAS,
            system=SNOMED_OID_ALIAS,
            code=SNOMED_T2DM_CODE,
        )
        assert r.status_code == 200
        out_system = _param_value(r.json(), "system")
        # CR-011: Out system is canonical, not the client alias input
        assert out_system == SNOMED_URI, (
            f"Out system via OID alias should resolve to canonical "
            f"({SNOMED_URI!r}); got {out_system!r}. "
            f"Client-input-as-canonical drift pattern (count=8 PROMOTED) "
            f"MUST NOT recur."
        )


# =============================================================================
# Lens 6: Cross-resource clinical consistency (canonical-DISPLAY META-PATTERN)
# =============================================================================


class TestLens6CrossResourceClinicalConsistency:
    """Verify VS-$validate-code Out display matches $lookup Out display
    for every seeded code (canonical-DISPLAY META-PATTERN clinical-
    correctness dimension).
    """

    @pytest.mark.parametrize(
        "system, code, expected_display",
        [
            (SNOMED_URI, SNOMED_DM_CODE, SNOMED_DM_DISPLAY),
            (SNOMED_URI, SNOMED_T2DM_CODE, SNOMED_T2DM_DISPLAY),
            (ICD10CM_URI, ICD10CM_E11_CODE, ICD10CM_E11_DISPLAY),
            (RXNORM_URI, RXNORM_METFORMIN_CODE, RXNORM_METFORMIN_DISPLAY),
        ],
    )
    def test_t60_vs_validate_display_matches_lookup_per_system(
        self, fhir_client, system, code, expected_display,
    ):
        """VS-$validate-code Out display byte-exact equals $lookup Out
        display for every seeded code. Clinical correctness: clients
        using either operation see the SAME canonical display for the
        same code.
        """
        vs_r = _validate_vs_get(fhir_client, system=system, code=code)
        lookup_display = _lookup_out_display(fhir_client, system, code)
        vs_display = _param_value(vs_r.json(), "display")
        assert vs_display == lookup_display == expected_display, (
            f"VS-$validate-code display {vs_display!r} should byte-exact "
            f"equal $lookup display {lookup_display!r} AND the engine "
            f"canonical STR {expected_display!r}"
        )

    @pytest.mark.parametrize(
        "system, code",
        [
            (SNOMED_URI, SNOMED_DM_CODE),
            (SNOMED_URI, SNOMED_T2DM_CODE),
            (ICD10CM_URI, ICD10CM_E11_CODE),
            (RXNORM_URI, RXNORM_METFORMIN_CODE),
        ],
    )
    def test_t61_vs_cs_display_byte_exact_parity_per_system(
        self, fhir_client, system, code,
    ):
        """VS-$validate-code Out display byte-exact equals CS-$validate-
        code Out display for every seeded code. Cross-handler parity on
        the display dimension.
        """
        vs_r = _validate_vs_get(fhir_client, system=system, code=code)
        cs_r = _validate_cs_get(fhir_client, system=system, code=code)
        vs_display = _param_value(vs_r.json(), "display")
        cs_display = _param_value(cs_r.json(), "display")
        assert vs_display == cs_display, (
            f"VS↔CS Out display byte-exact parity for ({system!r}, {code!r}): "
            f"VS={vs_display!r}, CS={cs_display!r}"
        )

    def test_t62_snomed_dm_t2dm_clinical_distinguishability_preserved(
        self, fhir_client,
    ):
        """VS-$validate-code distinguishes SNOMED DM (broader) from
        SNOMED T2DM (narrower) via their canonical displays. Clinical
        correctness: a CDS hook reading the response can interpret the
        concept at the right clinical granularity.
        """
        dm_r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_DM_CODE,
        )
        t2dm_r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
        )
        dm_display = _param_value(dm_r.json(), "display")
        t2dm_display = _param_value(t2dm_r.json(), "display")
        assert dm_display == SNOMED_DM_DISPLAY
        assert t2dm_display == SNOMED_T2DM_DISPLAY
        # Clinical distinguishability: DM != T2DM
        assert dm_display != t2dm_display, (
            f"DM display {dm_display!r} must be clinically distinguishable "
            f"from T2DM display {t2dm_display!r}"
        )


# =============================================================================
# Lens 7: Source-read structural contracts for clinical-correctness invariants
# =============================================================================


class TestLens7SourceReadContracts:
    """Source-read contracts for clinical-correctness invariants in
    ``_do_vs_validate``. Structural forms that survive refactors.
    """

    def test_t70_vs_validate_canonical_system_uri_helper_imported(self):
        """``canonical_system_uri`` IS imported in the fhir_api module.
        Load-bearing contract — without the import, the helper cannot
        be called from ``_do_vs_validate``.
        """
        module_src = _read_module_source()
        # Check for the import statement
        assert (
            "from medterm4ds.engines.fhir import" in module_src
            or "canonical_system_uri" in module_src
        ), (
            "canonical_system_uri helper must be imported from "
            "engines.fhir for the client-input-as-canonical drift "
            "fix to work."
        )

    def test_t71_vs_validate_canonical_system_uri_called_on_scalar_path(self):
        """``_do_vs_validate`` scalar path calls ``canonical_system_uri``
        before passing to ``build_parameters_validate``. CR-011 fix
        contract on the VS surface.
        """
        module_src = _read_module_source()
        vs_func = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_vs_validate",
        )
        assert vs_func is not None
        # The canonical_system_uri call MUST appear in the scalar branch
        assert "canonical_uri = canonical_system_uri" in vs_func, (
            "Scalar branch MUST call canonical_system_uri (CR-011 fix "
            "on VS surface; client-input-as-canonical drift count=8 "
            "PROMOTED)."
        )

    def test_t72_vs_validate_canonical_system_uri_called_on_cc_path(self):
        """``_do_vs_validate`` cc branch wraps matched_uri through
        ``canonical_system_uri``. CR-025 fix contract.
        """
        module_src = _read_module_source()
        vs_func = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_vs_validate",
        )
        assert vs_func is not None
        # CR-025 fix: canonical_matched_uri = canonical_system_uri(matched_uri)
        assert "canonical_matched_uri = (" in vs_func, (
            "cc branch MUST wrap matched_uri through canonical_system_uri "
            "(CR-025 fix; client-input-as-canonical drift count=8 "
            "PROMOTED sibling instance)."
        )

    def test_t73_vs_validate_no_broad_except_in_handler(self):
        """``_do_vs_validate`` MUST NOT contain ``except Exception`` —
        silent-fallback prohibition per GLOBAL_RULES.md.
        """
        module_src = _read_module_source()
        vs_func = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_vs_validate",
        )
        assert vs_func is not None
        # Walk AST and assert no broad except
        tree = ast.parse(vs_func)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert node.type is None or (
                    isinstance(node.type, ast.Name)
                    and node.type.id not in ("Exception", "BaseException")
                ), (
                    f"_do_vs_validate MUST NOT contain broad except; "
                    f"found 'except {ast.dump(node.type)}' at line {node.lineno}"
                )

    def test_t74_vs_validate_returns_build_parameters_validate_only(self):
        """``_do_vs_validate`` returns ONLY via build_parameters_validate
        or _fhir_error (no inline dict construction). Load-bearing
        contract for response-shape consistency.
        """
        module_src = _read_module_source()
        vs_func = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_vs_validate",
        )
        assert vs_func is not None
        tree = ast.parse(vs_func)
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and node.value is not None:
                # The return value should be a Call to build_parameters_validate
                # or _fhir_error
                if isinstance(node.value, ast.Call):
                    func = node.value.func
                    if isinstance(func, ast.Name):
                        assert func.id in (
                            "build_parameters_validate", "_fhir_error",
                        ), (
                            f"_do_vs_validate return at line {node.lineno} "
                            f"should call build_parameters_validate or "
                            f"_fhir_error; found {func.id}"
                        )

    def test_t75_vs_validate_message_format_byte_exact_template(self):
        """``_do_vs_validate`` scalar display-mismatch message uses the
        byte-exact format ``'The display "X" is incorrect'`` — mirror
        of CS-03 SKEPTIC QA-048 fix on the sibling CodeSystem handler.
        """
        module_src = _read_module_source()
        vs_func = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_vs_validate",
        )
        assert vs_func is not None
        assert (
            "'The display \"' in" not in vs_func  # naive
        )
        # The exact message template
        assert 'is incorrect' in vs_func, (
            "_do_vs_validate MUST contain display-mismatch message "
            "template 'The display \"X\" is incorrect' (mirror of CS-03 "
            "SKEPTIC QA-048)."
        )

    def test_t76_vs_validate_unknown_code_message_template(self):
        """``_do_vs_validate`` unknown-code message uses the template
        ``'Code {code} is not valid in code system {canonical_uri}.'``
        — clinically informative (cites both code AND canonical URI).
        """
        module_src = _read_module_source()
        vs_func = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_vs_validate",
        )
        assert vs_func is not None
        assert (
            "is not valid in code system" in vs_func
        ), (
            "_do_vs_validate unknown-code message MUST cite both code "
            "AND canonical system URI (clinical informativeness for "
            "CDS hooks)."
        )


# =============================================================================
# Lens 8: Clinical safety — no silent-wrong-answer on edge cases
# =============================================================================


class TestLens8ClinicalSafetyNoSilentWrongAnswer:
    """Clinical-safety probes: no silent-wrong-answer on edge cases that
    would mislead a CDS hook or clinician.
    """

    def test_t80_cc_with_unknown_system_only_skips_unknown(
        self, fhir_client,
    ):
        """cc with [unknown-system, valid-SNOMED-T2DM] → result=true;
        Out system+display reflects the VALID matched coding. No silent-
        wrong-answer on unknown-system codings in the cc.
        """
        body = _codeable_concept_post_body([
            ("http://example.org/unknown-system", "99999"),
            (SNOMED_URI, SNOMED_T2DM_CODE),
        ])
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        body_json = r.json()
        assert _param_value(body_json, "result") is True
        assert _param_value(body_json, "system") == SNOMED_URI
        assert _param_value(body_json, "display") == SNOMED_T2DM_DISPLAY

    def test_t81_cc_all_invalid_returns_false_with_informative_message(
        self, fhir_client,
    ):
        """cc with all-invalid codings → result=false; message explains
        'None of the codings in the codeableConcept are in the code system.'.
        Clinical correctness: CDS hook reading the message can surface
        the failure cause to the clinician.
        """
        body = _codeable_concept_post_body([
            (SNOMED_URI, "9999999999"),
            (ICD10CM_URI, "Z99.99"),
        ])
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        body_json = r.json()
        assert _param_value(body_json, "result") is False
        msg = _param_value(body_json, "message")
        assert msg is not None
        # Message is clinically informative — explains the failure
        assert "None of the codings" in msg, (
            f"cc-all-invalid message should explain the failure cause; "
            f"got {msg!r}"
        )

    def test_t82_unknown_system_returns_400_with_fhir_response(
        self, fhir_client,
    ):
        """Unknown system → 400 OperationOutcome, NOT 500 with text/plain.
        Clinical-safety: server returns a FHIR response shape so CDS hooks
        can parse the error uniformly.
        """
        r = _validate_vs_get(
            fhir_client,
            system="http://example.org/unknown-system",
            code="any-code",
        )
        assert r.status_code == 400
        assert r.headers["content-type"].startswith("application/fhir+json")
        body = r.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_t83_result_always_present_in_200_response(self, fhir_client):
        """Every 200 response from VS-$validate-code carries an Out
        ``result`` parameter. Clinical-safety: clients can rely on the
        ``result`` field being present.
        """
        # Multiple invocations — all MUST have result present
        responses = [
            _validate_vs_get(
                fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
            ),
            _validate_vs_get(
                fhir_client, system=SNOMED_URI, code="9999999999",
            ),
            _validate_vs_post(
                fhir_client,
                _codeable_concept_post_body([(SNOMED_URI, SNOMED_T2DM_CODE)]),
            ),
            _validate_vs_post(
                fhir_client,
                _codeable_concept_post_body([(SNOMED_URI, "9999999999")]),
            ),
        ]
        for r in responses:
            assert r.status_code == 200
            body = r.json()
            assert _has_param(body, "result"), (
                f"Out `result` MUST always be present in 200 response "
                f"(spec Out `result`: 1..1 boolean). Body: {body}"
            )


# =============================================================================
# Lens 9: Carry-forward reconfirmations
# =============================================================================


class TestLens9CarryForwardReconfirmations:
    """Re-confirm prior patterns and carry-forwards on the VS-05 surface.
    """

    def test_t90_client_input_as_canonical_drift_count_8_promoted(self):
        """client-input-as-canonical drift pattern (count=8 PROMOTED)
        re-derived HELD via source-read on both VS scalar + cc paths.
        """
        module_src = _read_module_source()
        vs_func = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_vs_validate",
        )
        assert vs_func is not None
        # Both scalar and cc paths MUST call canonical_system_uri
        assert "canonical_uri = canonical_system_uri" in vs_func, (
            "Scalar path MUST re-resolve through canonical_system_uri."
        )
        assert "canonical_matched_uri = (" in vs_func, (
            "cc path MUST re-resolve matched_uri through canonical_system_uri."
        )

    def test_t91_qa069_display_mismatch_enforcement_held(self, fhir_client):
        """VS-05 SKEPTIC QA-069 display mismatch enforcement re-derived
        HELD via behavioral probe.
        """
        r = _validate_vs_get(
            fhir_client,
            system=SNOMED_URI,
            code=SNOMED_T2DM_CODE,
            display="WRONG DISPLAY",
        )
        body = r.json()
        assert _param_value(body, "result") is False
        msg = _param_value(body, "message")
        assert "WRONG DISPLAY" in msg

    def test_t92_qa070_codeable_concept_all_pairs_held(self, fhir_client):
        """VS-05 SKEPTIC QA-070 codeableConcept all-pairs helper wired
        into ``_extract_vs_validate_params`` (batch dispatcher) —
        re-derived HELD.
        """
        module_src = _read_module_source()
        # _extract_vs_validate_params should call the all-pairs helper
        extract_func = _read_nested_function_source(
            module_src, "create_fhir_app", "_extract_vs_validate_params",
        )
        # The function may or may not be nested — try both forms
        if extract_func is None:
            # Search at module level
            tree = ast.parse(module_src)
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "_extract_vs_validate_params"
                ):
                    extract_func = ast.get_source_segment(tree, node)
                    break
        assert extract_func is not None, (
            "_extract_vs_validate_params function not found"
        )
        assert (
            "_extract_all_coding_pairs_from_codeable_concept" in extract_func
        ), (
            "_extract_vs_validate_params MUST call the all-pairs helper "
            "(VS-05 SKEPTIC QA-070)."
        )

    def test_t93_cs03_audit_002_status_intact(self, fhir_client):
        """CS-03 SKEPTIC AUDIT-002 status intact — cc path does NOT
        enforce display mismatch (spec-permitted non-enforcement).
        """
        body = _codeable_concept_post_body(
            [(SNOMED_URI, SNOMED_T2DM_CODE)],
            display="WRONG DISPLAY ON MATCHED CODING",
        )
        r = _validate_vs_post(fhir_client, body)
        # Per AUDIT-002: result=true, no mismatch triggered
        assert _param_value(r.json(), "result") is True

    def test_t94_cf_explorer_cs02_01_fully_closed_status(self):
        """CF-EXPLORER-CS02-01 FULLY CLOSED — POST /fhir/ValueSet/$validate-code
        route IS registered.
        """
        module_src = _read_module_source()
        # The route MUST be registered
        assert '"/fhir/ValueSet/$validate-code"' in module_src, (
            "POST route /fhir/ValueSet/$validate-code MUST be registered "
            "(CF-EXPLORER-CS02-01 FULLY CLOSED across every operation)."
        )

    def test_t95_cf_historian_vs02_02_resolved_canonical_helper_intact(self):
        """CF-HISTORIAN-VS02-02 RESOLVED — canonical_system_uri helper
        used on the implicit VS URL path. Mirror verification on VS-05
        surface.
        """
        module_src = _read_module_source()
        # The helper is imported and called
        assert "canonical_system_uri" in module_src
        # Specific to VS-05: the scalar path call shape mirrors the
        # implicit VS URL path call shape (CR-011 fix)
        vs_func = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_vs_validate",
        )
        assert vs_func is not None
        assert (
            "canonical_uri = canonical_system_uri(system_uri, source=source)"
            in vs_func
        ), (
            "VS scalar path MUST pass source= kwarg to canonical_system_uri "
            "(CR-011 fix shape; mirrors implicit VS URL path fix)."
        )
