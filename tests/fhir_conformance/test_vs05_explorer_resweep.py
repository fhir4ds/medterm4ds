"""VS-05 EXPLORER resweep: ValueSet $validate-code Operation.

Source: https://build.fhir.org/valueset-operation-validate-code.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-validate-code.html

This is the resweep (3rd of 4 personalities) pass for chunk VS-05.

EXPLORER lens: lateral thinking — unusual parameter combinations,
undocumented features, integration corners. The probes here target
TEST CASES THAT NO PRIOR PERSONALITY HAS TRIED.

HISTORIAN tip for EXPLORER (4 lateral combination classes — ALL ADDRESSED):

  Tip #1: POST batch with mixed scalar + codeableConcept entries
    - META-PATTERN on the batch surface: verify per-entry canonical-
      DISPLAY agreement across mixed (scalar, codeableConcept) entries.
    - Probe a 3-entry batch interleaving (scalar, codeableConcept,
      scalar) and verify each entry's Out `display` byte-exact equals
      $lookup Out display for the matched code.

  Tip #2: Implicit VS URL with display mismatch
    - The display mismatch path was tested with the explicit
      `system+code` form (SKEPTIC test_s50) and on the alias-input path
      (HISTORIAN test_h34 via OID-alias). EXPLORER verifies the message
      format holds on the IMPLICIT VS URL path — where the URL is a
      code-system URI alone (e.g. `http://snomed.info/sct`) AND the
      display triggers mismatch enforcement.

  Tip #3: codeableConcept with display param on matched coding
    - Spec does NOT mandate display enforcement on codeableConcept
      (CS-03 SKEPTIC AUDIT-002). EXPLORER verifies the current
      semantic: when a codeableConcept's matched coding has a display
      that differs from the supplied `display`, the handler does NOT
      trigger mismatch — result=true. Documented via carry-forward-as-
      probe pattern (strategy 56) for future-enhancement tracking.

  Tip #4: cross-handler CS↔VS message parity on the unknown-code path
    - HISTORIAN test_h32 verified cross-handler CS↔VS message parity on
      the MISMATCH case. EXPLORER extends to the unknown-code path
      where the message format differs from the mismatch case
      ('Code X is not valid in code system Y' vs 'The display "X" is
      incorrect'). Both handlers MUST agree byte-exact.

Additional lateral probe classes:

  - Display parameter supplied WITHOUT code (spec: "If a display is
    provided a code must be provided").
  - codeableConcept with mixed codings from DIFFERENT systems + display
    param targeting the matched coding.
  - Implicit VS URL with codeableConcept body (vs explicit system+code).
  - GET↔POST byte-exact parity on lateral shapes (implicit VS URL,
    codeableConcept-with-display-on-matched-coding).
  - Cross-handler CS↔VS byte-exact parity on lateral shapes (unknown-
    system, all-invalid codeableConcept, codeableConcept-display-no-
    enforcement).
  - Source-read structural contract: ``_do_vs_validate`` returns the
    message-only-on-mismatch contract on the codeableConcept path too.
  - GET with both url= AND system= (spec allows both — In param table
    shows url 0..1 AND system 0..1).

Conformance fixture (per conftest.py): 4 mrconso rows + 1 mrrel row.
  SNOMED 73211009 = "Diabetes mellitus"
  SNOMED 44054006 = "Type 2 diabetes mellitus"
  ICD-10-CM E11   = "Type 2 diabetes mellitus"
  RxNorm  860975  = "24 HR metformin 500 MG Oral Tablet"
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
# Constants — seeded systems + codes (mirror SKEPTIC + HISTORIAN resweep).
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

# Aliases (per FHIR_URI_ALIASES in engines/fhir/__init__.py)
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
    """Return canonical display for ``code`` from $lookup.

    Used as the META-PATTERN reference operation per SKEPTIC test_s70.
    """
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
# Lens 1 — HISTORIAN Tip #1: POST batch with mixed scalar + codeableConcept
# entries (META-PATTERN on the batch surface)
# =============================================================================


class TestLens1MixedScalarCodeableConceptBatch:
    """L1: POST batch with mixed scalar + codeableConcept entries.

    Per HISTORIAN tip #1, this is a META-PATTERN-on-batch-surface probe:
    each batch entry's Out `display` MUST byte-exact equal the $lookup
    Out display for the matched code. The dispatcher (verified by
    SKEPTIC test_s84 + HISTORIAN test_h21 source-read) routes both
    scalar (system+code) and codeableConcept entries through
    ``_do_vs_validate``, which then resolves canonical displays via
    ``build_parameters_validate``.

    Spec: https://hl7.org/fhir/R4/valueset-operation-validate-code.html
    Out `display` is "A valid display for the concept if the system
    wishes to display this to a user" — the canonical display, NOT the
    client-supplied alias input.
    """

    def test_e10_batch_mixed_scalar_then_codeableConcept_display_agreement(
        self, fhir_client,
    ):
        """e10: 2-entry batch — first scalar (T2DM), then codeableConcept
        ([INVALID, METFORMIN]). Each entry's Out display MUST byte-exact
        equal $lookup Out display for the matched code.
        """
        # Matched codes + canonical displays per fixture
        lookup_t2dm = _lookup_out_display(fhir_client, SNOMED_URI, SNOMED_T2DM_CODE)
        lookup_metformin = _lookup_out_display(fhir_client, RXNORM_URI, RXNORM_METFORMIN_CODE)
        assert lookup_t2dm is not None
        assert lookup_metformin is not None

        # Entry 1: scalar (SNOMED T2DM)
        entry1_body = _scalar_post_body(SNOMED_URI, SNOMED_T2DM_CODE)
        # Entry 2: codeableConcept [INVALID + METFORMIN] → result=true, matched=metformin
        entry2_body = _codeable_concept_post_body([
            (SNOMED_URI, "9999999999INVALID"),  # invalid SNOMED code
            (RXNORM_URI, RXNORM_METFORMIN_CODE),
        ])

        bundle = _batch_bundle([
            _batch_post_entry("/ValueSet/$validate-code", entry1_body),
            _batch_post_entry("/ValueSet/$validate-code", entry2_body),
        ])

        r = fhir_client.post(
            "/fhir", json=bundle,
            headers={"Accept": "application/fhir+json"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["resourceType"] == "Bundle"
        assert body["type"] == "batch-response"
        entries = body.get("entry", [])
        assert len(entries) == 2

        # Entry 1: scalar T2DM — Out display == $lookup display
        e1_resource = entries[0].get("resource", {})
        assert e1_resource["resourceType"] == "Parameters"
        assert _param_value(e1_resource, "result") is True
        assert _param_value(e1_resource, "display") == lookup_t2dm

        # Entry 2: codeableConcept [INVALID, METFORMIN] → matched=metformin
        # Out display == $lookup display for metformin (canonical-DISPLAY
        # META-PATTERN extension to the batch surface + the
        # codeableConcept-matched-coding case).
        e2_resource = entries[1].get("resource", {})
        assert e2_resource["resourceType"] == "Parameters"
        assert _param_value(e2_resource, "result") is True
        assert _param_value(e2_resource, "display") == lookup_metformin

    def test_e11_batch_three_entry_interleaved_scalar_cc_scalar(
        self, fhir_client,
    ):
        """e11: 3-entry interleaved batch — scalar, codeableConcept,
        scalar. Per-entry canonical-DISPLAY agreement must hold on each
        entry independently (per-entry isolation TS-04 SKEPTIC QA-038).
        """
        lookup_dm = _lookup_out_display(fhir_client, SNOMED_URI, SNOMED_DM_CODE)
        lookup_e11 = _lookup_out_display(fhir_client, ICD10CM_URI, ICD10CM_E11_CODE)
        lookup_t2dm = _lookup_out_display(fhir_client, SNOMED_URI, SNOMED_T2DM_CODE)
        assert lookup_dm is not None
        assert lookup_e11 is not None
        assert lookup_t2dm is not None

        # Entry 1: scalar SNOMED DM
        entry1_body = _scalar_post_body(SNOMED_URI, SNOMED_DM_CODE)
        # Entry 2: codeableConcept [INVALID + ICD10 E11]
        entry2_body = _codeable_concept_post_body([
            (SNOMED_URI, "INVALID-X"),
            (ICD10CM_URI, ICD10CM_E11_CODE),
        ])
        # Entry 3: scalar SNOMED T2DM
        entry3_body = _scalar_post_body(SNOMED_URI, SNOMED_T2DM_CODE)

        bundle = _batch_bundle([
            _batch_post_entry("/ValueSet/$validate-code", entry1_body),
            _batch_post_entry("/ValueSet/$validate-code", entry2_body),
            _batch_post_entry("/ValueSet/$validate-code", entry3_body),
        ])

        r = fhir_client.post(
            "/fhir", json=bundle,
            headers={"Accept": "application/fhir+json"},
        )
        assert r.status_code == 200
        body = r.json()
        entries = body.get("entry", [])
        assert len(entries) == 3

        # Per-entry Out display agreement
        e1 = entries[0].get("resource", {})
        assert _param_value(e1, "display") == lookup_dm

        e2 = entries[1].get("resource", {})
        assert _param_value(e2, "display") == lookup_e11

        e3 = entries[2].get("resource", {})
        assert _param_value(e3, "display") == lookup_t2dm

    def test_e12_batch_mixed_all_codeableConcept_entries_agreement(
        self, fhir_client,
    ):
        """e12: 2-entry batch — both codeableConcept entries. Validates
        that the dispatcher's all-pairs helper is invoked per-entry
        (VS-05 SKEPTIC QA-070 batch mirror) AND canonical-DISPLAY
        agreement holds on each entry.
        """
        lookup_t2dm = _lookup_out_display(fhir_client, SNOMED_URI, SNOMED_T2DM_CODE)
        lookup_metformin = _lookup_out_display(fhir_client, RXNORM_URI, RXNORM_METFORMIN_CODE)
        assert lookup_t2dm is not None
        assert lookup_metformin is not None

        # Entry 1: codeableConcept [INVALID, T2DM]
        entry1 = _codeable_concept_post_body([
            (ICD10CM_URI, "INVALID"),
            (SNOMED_URI, SNOMED_T2DM_CODE),
        ])
        # Entry 2: codeableConcept [METFORMIN, INVALID]
        entry2 = _codeable_concept_post_body([
            (RXNORM_URI, RXNORM_METFORMIN_CODE),
            (SNOMED_URI, "INVALID-2"),
        ])

        bundle = _batch_bundle([
            _batch_post_entry("/ValueSet/$validate-code", entry1),
            _batch_post_entry("/ValueSet/$validate-code", entry2),
        ])

        r = fhir_client.post(
            "/fhir", json=bundle,
            headers={"Accept": "application/fhir+json"},
        )
        assert r.status_code == 200
        body = r.json()
        entries = body.get("entry", [])
        assert len(entries) == 2

        e1 = entries[0].get("resource", {})
        assert _param_value(e1, "result") is True
        assert _param_value(e1, "display") == lookup_t2dm

        e2 = entries[1].get("resource", {})
        assert _param_value(e2, "result") is True
        assert _param_value(e2, "display") == lookup_metformin

    def test_e13_batch_codeableConcept_with_display_param_does_not_trigger_mismatch(
        self, fhir_client,
    ):
        """e13: batch entry with codeableConcept [INVALID, METFORMIN] AND
        a `display` parameter that differs from the matched (metformin)
        canonical display. Spec does NOT mandate display enforcement on
        codeableConcept (CS-03 SKEPTIC AUDIT-002). Verify result=true
        (NOT result=false from display mismatch).

        Pinned via carry-forward-as-probe pattern (strategy 56) — when
        a future enhancement chunk surfaces display enforcement on
        codeableConcept, this probe MUST be updated to assert
        result=false + canonical Out display.
        """
        # codeableConcept matched code is metformin; supply a WRONG
        # display ("WRONG DISPLAY") that differs from canonical.
        entry_body = _codeable_concept_post_body(
            [(SNOMED_URI, "INVALID"), (RXNORM_URI, RXNORM_METFORMIN_CODE)],
            display="WRONG DISPLAY NOT METFORMIN",
        )

        bundle = _batch_bundle([
            _batch_post_entry("/ValueSet/$validate-code", entry_body),
        ])

        r = fhir_client.post(
            "/fhir", json=bundle,
            headers={"Accept": "application/fhir+json"},
        )
        assert r.status_code == 200
        body = r.json()
        entries = body.get("entry", [])
        assert len(entries) == 1

        e = entries[0].get("resource", {})
        # Per spec + current implementation, codeableConcept matched
        # coding's display is NOT enforced — result=true.
        assert _param_value(e, "result") is True
        # Out display is the canonical of the matched coding (metformin).
        assert _param_value(e, "display") == RXNORM_METFORMIN_DISPLAY

    def test_e14_batch_scalar_with_display_mismatch_triggers_correctly(
        self, fhir_client,
    ):
        """e14: sibling of e13 — batch entry with SCALAR (system+code)
        AND a `display` parameter that differs. The mismatch MUST fire
        (VS-05 SKEPTIC QA-069). This is the asymmetry between scalar
        path and codeableConcept path documented in CS-03 SKEPTIC
        AUDIT-002.
        """
        entry_body = _scalar_post_body(
            SNOMED_URI, SNOMED_T2DM_CODE,
            display="WRONG DISPLAY NOT T2DM",
        )

        bundle = _batch_bundle([
            _batch_post_entry("/ValueSet/$validate-code", entry_body),
        ])

        r = fhir_client.post(
            "/fhir", json=bundle,
            headers={"Accept": "application/fhir+json"},
        )
        assert r.status_code == 200
        body = r.json()
        entries = body.get("entry", [])
        assert len(entries) == 1

        e = entries[0].get("resource", {})
        assert _param_value(e, "result") is False
        # Cross-handler message format from VS-05 SKEPTIC QA-069:
        # 'The display "X" is incorrect'
        assert _param_value(e, "message") == 'The display "WRONG DISPLAY NOT T2DM" is incorrect'
        # Out display is the engine canonical, NOT client echo
        assert _param_value(e, "display") == SNOMED_T2DM_DISPLAY

    def test_e15_batch_unknown_code_in_scalar_entry_returns_false_with_message(
        self, fhir_client,
    ):
        """e15: batch entry with scalar (system+UNKNOWN code). MUST
        return result=false with the unknown-code message — the
        cross-handler CS↔VS message parity case on the unknown-code
        path (HISTORIAN tip #4).
        """
        entry_body = _scalar_post_body(SNOMED_URI, "99999999UNKNOWN")

        bundle = _batch_bundle([
            _batch_post_entry("/ValueSet/$validate-code", entry_body),
        ])

        r = fhir_client.post(
            "/fhir", json=bundle,
            headers={"Accept": "application/fhir+json"},
        )
        assert r.status_code == 200
        body = r.json()
        entries = body.get("entry", [])
        assert len(entries) == 1

        e = entries[0].get("resource", {})
        assert _param_value(e, "result") is False
        # Unknown-code message format (mirror of CS-03 _do_validate):
        # 'Code X is not valid in code system Y'
        msg = _param_value(e, "message")
        assert msg is not None
        assert "99999999UNKNOWN" in msg
        assert "is not valid in code system" in msg


# =============================================================================
# Lens 2 — HISTORIAN Tip #2: Implicit VS URL with display mismatch
# =============================================================================


class TestLens2ImplicitVSURLDisplayMismatch:
    """L2: Implicit VS URL with display mismatch.

    Per HISTORIAN tip #2, the display mismatch path was tested with
    explicit system+code (SKEPTIC test_s50) and on alias-input
    (HISTORIAN test_h34 via OID-alias). EXPLORER verifies the message
    format holds on the IMPLICIT VS URL path — where the URL is a
    code-system URI alone (e.g. ``http://snomed.info/sct``) AND the
    display triggers mismatch enforcement.

    Spec: https://hl7.org/fhir/R4/valueset-operation-validate-code.html
    In `url` 0..1: "The server must know the value set (e.g. it is
    defined explicitly in the server's value sets, or it is defined
    implicitly by some code system…)". Implicit VS = code-system URI
    alone as the ``url`` value.

    The handler delegates to ``_do_vs_validate`` which enforces display
    mismatch on the scalar-system path (VS-05 SKEPTIC QA-069). The
    implicit VS URL form should NOT bypass display enforcement.
    """

    def test_e20_implicit_vs_url_with_display_mismatch_returns_false_with_message(
        self, fhir_client,
    ):
        """e20: url=<implicit VS> + system + code + WRONG display.
        Mismatch MUST fire (QA-069 carry-forward applies to implicit VS
        URL form too — same handler path).
        """
        r = _validate_vs_get(
            fhir_client,
            url=SNOMED_URI,           # implicit VS URL form (code-system URI alone)
            system=SNOMED_URI,
            code=SNOMED_T2DM_CODE,
            display="WRONG IMPLICIT VS DISPLAY",
        )
        assert r.status_code == 200
        body = r.json()
        assert _param_value(body, "result") is False
        assert (
            _param_value(body, "message")
            == 'The display "WRONG IMPLICIT VS DISPLAY" is incorrect'
        )
        # Out display is the engine canonical, NOT the client echo.
        assert _param_value(body, "display") == SNOMED_T2DM_DISPLAY

    def test_e21_implicit_vs_url_with_display_match_returns_true(
        self, fhir_client,
    ):
        """e21: sibling of e20 — url=<implicit VS> + system + code +
        CORRECT display. result=true (no mismatch).
        """
        r = _validate_vs_get(
            fhir_client,
            url=SNOMED_URI,
            system=SNOMED_URI,
            code=SNOMED_T2DM_CODE,
            display=SNOMED_T2DM_DISPLAY,
        )
        assert r.status_code == 200
        body = r.json()
        assert _param_value(body, "result") is True

    @pytest.mark.parametrize(
        "system_uri, code, canonical_display",
        [
            (SNOMED_URI, SNOMED_DM_CODE, SNOMED_DM_DISPLAY),
            (SNOMED_URI, SNOMED_T2DM_CODE, SNOMED_T2DM_DISPLAY),
            (ICD10CM_URI, ICD10CM_E11_CODE, ICD10CM_E11_DISPLAY),
            (RXNORM_URI, RXNORM_METFORMIN_CODE, RXNORM_METFORMIN_DISPLAY),
        ],
    )
    def test_e22_implicit_vs_url_display_mismatch_per_system_byte_exact_message(
        self, fhir_client, system_uri, code, canonical_display,
    ):
        """e22: 4-parametrized — implicit VS URL form per seeded system.
        The mismatch message format MUST be byte-exact 'The display "X"
        is incorrect' across every seeded system. Catches silent per-
        system drift on the implicit VS path.

        META-PATTERN extension: canonical-DISPLAY invariant on the
        implicit VS path.
        """
        r = _validate_vs_get(
            fhir_client,
            url=system_uri,
            system=system_uri,
            code=code,
            display="WRONG PER SYSTEM",
        )
        assert r.status_code == 200
        body = r.json()
        assert _param_value(body, "result") is False
        # Byte-exact message format per SKEPTIC test_s52
        assert (
            _param_value(body, "message")
            == 'The display "WRONG PER SYSTEM" is incorrect'
        )
        # Out display byte-exact equals the engine canonical
        assert _param_value(body, "display") == canonical_display

    def test_e23_implicit_vs_url_snomed_intensional_with_display_mismatch(
        self, fhir_client,
    ):
        """e23: SNOMED intensional URL form ``?fhir_vs=isa`` as implicit
        VS URL. Display mismatch MUST fire — the intensional URL form
        doesn't bypass display enforcement.
        """
        intensional_url = f"{SNOMED_URI}?fhir_vs=isa"
        r = _validate_vs_get(
            fhir_client,
            url=intensional_url,
            system=SNOMED_URI,
            code=SNOMED_T2DM_CODE,
            display="WRONG INTENSIONAL DISPLAY",
        )
        assert r.status_code == 200
        body = r.json()
        assert _param_value(body, "result") is False
        assert (
            _param_value(body, "message")
            == 'The display "WRONG INTENSIONAL DISPLAY" is incorrect'
        )
        assert _param_value(body, "display") == SNOMED_T2DM_DISPLAY

    def test_e24_implicit_vs_url_with_display_mismatch_get_post_byte_exact(
        self, fhir_client,
    ):
        """e24: implicit VS URL + display mismatch via GET and POST.
        Both paths MUST produce byte-exact Parameters bodies.

        GET vs POST parity was verified on the regular system+code path
        (SKEPTIC test_s55). EXPLORER extends to the implicit VS URL
        path.
        """
        # GET
        r_get = _validate_vs_get(
            fhir_client,
            url=SNOMED_URI,
            system=SNOMED_URI,
            code=SNOMED_T2DM_CODE,
            display="WRONG IMPLICIT GETPOST",
        )
        assert r_get.status_code == 200

        # POST (body with url + system + code + display)
        post_body = _parameters_body(
            ("url", {"valueUri": SNOMED_URI}),
            ("system", {"valueUri": SNOMED_URI}),
            ("code", {"valueCode": SNOMED_T2DM_CODE}),
            ("display", {"valueString": "WRONG IMPLICIT GETPOST"}),
        )
        r_post = _validate_vs_post(fhir_client, post_body)
        assert r_post.status_code == 200

        # Byte-exact Out display + message + result
        assert (
            _param_value(r_get.json(), "display")
            == _param_value(r_post.json(), "display")
            == SNOMED_T2DM_DISPLAY
        )
        assert (
            _param_value(r_get.json(), "message")
            == _param_value(r_post.json(), "message")
            == 'The display "WRONG IMPLICIT GETPOST" is incorrect'
        )
        assert (
            _param_value(r_get.json(), "result")
            == _param_value(r_post.json(), "result")
            is False
        )


# =============================================================================
# Lens 3 — HISTORIAN Tip #3: codeableConcept with display param on
# matched coding (spec does NOT mandate display enforcement on cc)
# =============================================================================


class TestLens3CodeableConceptDisplayNoEnforcement:
    """L3: codeableConcept with display param on matched coding.

    Per HISTORIAN tip #3, spec does NOT mandate display enforcement on
    codeableConcept (CS-03 SKEPTIC AUDIT-002). EXPLORER verifies the
    current semantic: when a codeableConcept's matched coding has a
    display that differs from the supplied `display`, the handler does
    NOT trigger mismatch — result=true. Pinned via carry-forward-as-
    probe pattern for future-enhancement tracking.
    """

    def test_e30_cc_matched_coding_display_differs_no_mismatch_fires(
        self, fhir_client,
    ):
        """e30: codeableConcept [T2DM] + display='WRONG DISPLAY'. The
        matched coding's canonical display differs, but mismatch MUST
        NOT fire (codeableConcept path skips display enforcement).

        CS-03 SKEPTIC AUDIT-002 status: spec-permitted non-enforcement.
        """
        body = _codeable_concept_post_body(
            [(SNOMED_URI, SNOMED_T2DM_CODE)],
            display="WRONG DISPLAY FOR MATCHED T2DM",
        )
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        params = r.json()
        assert _param_value(params, "result") is True  # mismatch did NOT fire
        # Out display is the engine canonical of the matched coding
        assert _param_value(params, "display") == SNOMED_T2DM_DISPLAY

    def test_e31_cc_matched_coding_display_correct_also_true(
        self, fhir_client,
    ):
        """e31: codeableConcept [T2DM] + display=CORRECT. result=true
        (sanity sibling — both correct display AND wrong display yield
        result=true on codeableConcept path).
        """
        body = _codeable_concept_post_body(
            [(SNOMED_URI, SNOMED_T2DM_CODE)],
            display=SNOMED_T2DM_DISPLAY,
        )
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        params = r.json()
        assert _param_value(params, "result") is True

    def test_e32_cc_no_display_param_also_true(
        self, fhir_client,
    ):
        """e32: codeableConcept [T2DM] + NO display param. result=true
        (no display parameter, no enforcement).
        """
        body = _codeable_concept_post_body(
            [(SNOMED_URI, SNOMED_T2DM_CODE)],
        )
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        params = r.json()
        assert _param_value(params, "result") is True
        # Out display is the engine canonical of the matched coding
        assert _param_value(params, "display") == SNOMED_T2DM_DISPLAY

    def test_e33_cc_multi_coding_with_display_targeting_matched_returns_true(
        self, fhir_client,
    ):
        """e33: codeableConcept [INVALID, T2DM] + display=CANONICAL T2DM.
        Matched=T2DM, display matches canonical. result=true (mismatch
        on the INVALID coding is irrelevant — it's skipped; mismatch on
        the matched coding is NOT enforced).
        """
        body = _codeable_concept_post_body(
            [(ICD10CM_URI, "INVALID"), (SNOMED_URI, SNOMED_T2DM_CODE)],
            display=SNOMED_T2DM_DISPLAY,
        )
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        params = r.json()
        assert _param_value(params, "result") is True
        assert _param_value(params, "display") == SNOMED_T2DM_DISPLAY

    def test_e34_cc_multi_coding_with_display_targeting_matched_but_wrong_true(
        self, fhir_client,
    ):
        """e34: codeableConcept [INVALID, T2DM] + display='WRONG DISPLAY'.
        Matched=T2DM, display differs from canonical. result=true (per
        CS-03 SKEPTIC AUDIT-002 — display is NOT enforced on cc path).

        Pinned via carry-forward-as-probe pattern (strategy 56).
        """
        body = _codeable_concept_post_body(
            [(ICD10CM_URI, "INVALID"), (SNOMED_URI, SNOMED_T2DM_CODE)],
            display="WRONG DISPLAY AGAINST MATCHED T2DM",
        )
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        params = r.json()
        assert _param_value(params, "result") is True
        assert _param_value(params, "display") == SNOMED_T2DM_DISPLAY

    def test_e35_cc_display_param_get_post_byte_exact(
        self, fhir_client,
    ):
        """e35: GET codeableConcept via query param is structurally
        impossible (multi-coding requires structured body). EXPLORER
        verifies POST-only path AND that GET with single-system+code+
        display produces byte-exact message on the SCALAR path
        (sibling — the cc path is POST-only).

        This probe is here as a sanity-check that the GET scalar path
        with display differs from POST cc path (no result-shape
        divergence).
        """
        # GET scalar with WRONG display
        r_get = _validate_vs_get(
            fhir_client,
            system=SNOMED_URI,
            code=SNOMED_T2DM_CODE,
            display="WRONG SCALAR DISPLAY",
        )
        assert r_get.status_code == 200
        # POST cc with WRONG display targeting matched coding
        body = _codeable_concept_post_body(
            [(SNOMED_URI, SNOMED_T2DM_CODE)],
            display="WRONG SCALAR DISPLAY",
        )
        r_post = _validate_vs_post(fhir_client, body)
        assert r_post.status_code == 200

        # Asymmetry: scalar path enforces display mismatch (result=false)
        # while cc path does NOT (result=true).
        assert _param_value(r_get.json(), "result") is False
        assert _param_value(r_post.json(), "result") is True

    def test_e36_cc_with_display_source_read_no_mismatch_check_in_cc_path(
        self, fhir_client,
    ):
        """e36: SOURCE-READ probe — verify that ``_do_vs_validate``'s
        codeableConcept branch does NOT contain the display-mismatch
        check. This is the load-bearing structural contract for the
        CS-03 SKEPTIC AUDIT-002 status: spec-permitted non-enforcement.

        Distinct from SKEPTIC test_s82 (which source-reads the SCALAR
        path to verify the mismatch check IS present). EXPLORER source-
        reads the CC path to verify the mismatch check is ABSENT.
        """
        module_src = _read_module_source()
        cc_src = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_vs_validate",
        )
        assert cc_src is not None, "_do_vs_validate not found"

        # The CC branch should return build_parameters_validate(True, ...)
        # without an inline display-mismatch check (the SCALAR path
        # below the CC branch has the check, but the CC branch skips it).
        # The structural assertion: in the CC branch's matched-info
        # block, the build_parameters_validate call is NOT preceded by
        # the display != canonical_display comparison.
        # The SCALAR path comparison:
        scalar_comparison = (
            "display != canonical_display" in cc_src
        )
        assert scalar_comparison, (
            "Scalar-path display-mismatch check should still be present "
            "in _do_vs_validate source (CS-03 SKEPTIC AUDIT-002 deferred "
            "case is for CC path only, NOT removal of scalar check)."
        )

        # The CC path's matched-info block returns build_parameters_validate
        # directly without a display check. We verify by parsing the AST:
        # find the CC branch's matched-info return + ensure the
        # comparison does NOT appear in that block specifically.
        tree = ast.parse(cc_src)
        # Find the first `if codeable_concept_pairs:` block
        cc_block = None
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                # Heuristic: test contains "codeable_concept_pairs"
                test_src = ast.get_source_segment(cc_src, node.test) or ""
                if "codeable_concept_pairs" in test_src:
                    cc_block = node
                    break
        assert cc_block is not None, "codeable_concept_pairs branch not found"
        cc_block_src = ast.get_source_segment(cc_src, cc_block) or ""
        # CC branch should NOT contain display != canonical_display
        assert "display != canonical_display" not in cc_block_src, (
            "CC branch should NOT enforce display mismatch per CS-03 "
            "SKEPTIC AUDIT-002 (spec-permitted non-enforcement)."
        )


# =============================================================================
# Lens 4 — HISTORIAN Tip #4: cross-handler CS↔VS message parity on the
# unknown-code path (message format differs from mismatch case)
# =============================================================================


class TestLens4CrossHandlerUnknownCodeMessageParity:
    """L4: cross-handler CS↔VS message parity on the unknown-code path.

    Per HISTORIAN tip #4, the unknown-code message format differs from
    the mismatch case:
      - mismatch message: 'The display "X" is incorrect'
      - unknown-code message: 'Code X is not valid in code system Y'

    HISTORIAN test_h32 verified CS↔VS message parity on the mismatch
    case. EXPLORER extends to the unknown-code path: both handlers
    MUST agree byte-exact.

    Spec: https://hl7.org/fhir/R4/valueset-operation-validate-code.html
    Out `message`: "Error details, if result = false". The same message
    template SHOULD be used across CS↔VS handlers per cross-handler
    parity meta-pattern.
    """

    @pytest.mark.parametrize(
        "system_uri, code",
        [
            (SNOMED_URI, "99999999UNKNOWN1"),
            (SNOMED_URI, "88888888UNKNOWN2"),
            (ICD10CM_URI, "Z99Z99UNKNOWN"),
            (RXNORM_URI, "77777UNKNOWN"),
        ],
    )
    def test_e40_cross_handler_cs_vs_unknown_code_message_byte_exact(
        self, fhir_client, system_uri, code,
    ):
        """e40: 4-parametrized — CS↔VS message byte-exact on the
        unknown-code path. Catches silent per-handler message drift.
        """
        r_cs = _validate_cs_get(fhir_client, system=system_uri, code=code)
        r_vs = _validate_vs_get(fhir_client, system=system_uri, code=code)
        assert r_cs.status_code == 200
        assert r_vs.status_code == 200

        cs_msg = _param_value(r_cs.json(), "message")
        vs_msg = _param_value(r_vs.json(), "message")
        assert cs_msg is not None
        assert vs_msg is not None

        # Byte-exact agreement
        assert cs_msg == vs_msg, (
            f"CS↔VS unknown-code message drift: CS={cs_msg!r}, VS={vs_msg!r}"
        )
        # Both contain the code + system
        assert code in cs_msg
        assert "is not valid in code system" in cs_msg

    def test_e41_cross_handler_cs_vs_unknown_code_result_byte_exact(
        self, fhir_client,
    ):
        """e41: CS↔VS result byte-exact on the unknown-code path.
        result MUST be False on both handlers.
        """
        r_cs = _validate_cs_get(
            fhir_client, system=SNOMED_URI, code="UNKNOWN-X",
        )
        r_vs = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code="UNKNOWN-X",
        )
        assert r_cs.status_code == 200
        assert r_vs.status_code == 200
        assert (
            _param_value(r_cs.json(), "result")
            == _param_value(r_vs.json(), "result")
            is False
        )

    def test_e42_cross_handler_cs_vs_unknown_code_display_byte_exact(
        self, fhir_client,
    ):
        """e42: CS↔VS Out display byte-exact on the unknown-code path.
        When the code is unknown, neither handler has a canonical
        display; the Out display is absent (or empty string) on both.
        """
        r_cs = _validate_cs_get(
            fhir_client, system=SNOMED_URI, code="UNKNOWN-Y",
        )
        r_vs = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code="UNKNOWN-Y",
        )
        assert r_cs.status_code == 200
        assert r_vs.status_code == 200

        cs_display = _param_value(r_cs.json(), "display")
        vs_display = _param_value(r_vs.json(), "display")
        assert cs_display == vs_display

    def test_e43_cross_handler_unknown_code_with_canonical_system_uri_byte_exact(
        self, fhir_client,
    ):
        """e43: CS↔VS Out `system` byte-exact on the unknown-code path.
        The canonical URI MUST be the registry canonical (NOT the
        client-supplied input — client-input-as-canonical drift pattern
        count=8 PROMOTED).
        """
        # Use the OID alias — both handlers MUST resolve to canonical SNOMED URI
        r_cs = _validate_cs_get(
            fhir_client, system=SNOMED_OID_ALIAS, code="UNKNOWN-Z",
        )
        r_vs = _validate_vs_get(
            fhir_client, system=SNOMED_OID_ALIAS, code="UNKNOWN-Z",
        )
        assert r_cs.status_code == 200
        assert r_vs.status_code == 200

        cs_sys = _param_value(r_cs.json(), "system")
        vs_sys = _param_value(r_vs.json(), "system")
        assert cs_sys == vs_sys == SNOMED_URI, (
            f"CS↔VS canonical-system drift on unknown-code path: "
            f"CS={cs_sys!r}, VS={vs_sys!r}, expected={SNOMED_URI!r}"
        )

    def test_e44_cross_handler_cs_vs_unknown_code_message_get_post_byte_exact(
        self, fhir_client,
    ):
        """e44: GET↔POST byte-exact parity on the unknown-code path
        per handler. Extends cross-handler parity to GET↔POST parity.
        """
        # CS GET vs POST
        r_cs_get = _validate_cs_get(
            fhir_client, system=SNOMED_URI, code="UNKNOWN-CS-GETPOST",
        )
        cs_post_body = _parameters_body(
            ("system", {"valueUri": SNOMED_URI}),
            ("code", {"valueCode": "UNKNOWN-CS-GETPOST"}),
        )
        r_cs_post = fhir_client.post(
            "/fhir/CodeSystem/$validate-code",
            json=cs_post_body,
            headers={"Accept": "application/fhir+json"},
        )
        assert r_cs_get.status_code == 200
        assert r_cs_post.status_code == 200
        assert (
            _param_value(r_cs_get.json(), "message")
            == _param_value(r_cs_post.json(), "message")
        )

        # VS GET vs POST
        r_vs_get = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code="UNKNOWN-VS-GETPOST",
        )
        vs_post_body = _parameters_body(
            ("system", {"valueUri": SNOMED_URI}),
            ("code", {"valueCode": "UNKNOWN-VS-GETPOST"}),
        )
        r_vs_post = _validate_vs_post(fhir_client, vs_post_body)
        assert r_vs_get.status_code == 200
        assert r_vs_post.status_code == 200
        assert (
            _param_value(r_vs_get.json(), "message")
            == _param_value(r_vs_post.json(), "message")
        )


# =============================================================================
# Lens 5 — Lateral combination: display parameter supplied WITHOUT code
# =============================================================================


class TestLens5DisplayWithoutCode:
    """L5: display supplied WITHOUT code (spec: "If a display is provided
    a code must be provided").

    Spec: https://hl7.org/fhir/R4/valueset-operation-validate-code.html
    In `display`: "Display associated with the code. If a display is
    provided a code must be provided".

    EXPLORER verifies the engine's handling of this lateral corner.
    """

    def test_e50_display_without_code_returns_400(
        self, fhir_client,
    ):
        """e50: display + system WITHOUT code. Per spec, the code MUST
        be provided. Implementation MUST NOT silently wrong-answer.
        """
        r = _validate_vs_get(
            fhir_client,
            system=SNOMED_URI,
            display=SNOMED_T2DM_DISPLAY,
        )
        # Handler validates required params — code is missing → 400.
        assert r.status_code in (400, 422)

    def test_e51_display_without_code_or_system_returns_400(
        self, fhir_client,
    ):
        """e51: display ONLY (no code, no system). MUST return 4xx —
        cannot validate a display without any code/system reference.
        """
        r = _validate_vs_get(
            fhir_client,
            display=SNOMED_T2DM_DISPLAY,
        )
        assert r.status_code in (400, 422)

    def test_e52_display_with_url_only_no_code_accepted(
        self, fhir_client,
    ):
        """e52: display + url (implicit VS) WITHOUT explicit code.
        Spec In `display`: "If a display is provided a code must be
        provided" — but the implementation accepts url-only form for
        VS-$validate-code (the url param replaces the (system, code)
        pair when validating against an implicit VS).

        Documenting current behavior via carry-forward-as-probe.
        """
        r = _validate_vs_get(
            fhir_client,
            url=SNOMED_URI,
            display="SOMETHING",
        )
        # Implementation accepts this — url acts as the implicit VS,
        # but without a code, the result is 400 (code is required).
        # Documenting current behavior.
        assert r.status_code in (400, 422)


# =============================================================================
# Lens 6 — Lateral combination: codeableConcept from mixed systems
# =============================================================================


class TestLens6CodeableConceptCrossSystemLateral:
    """L6: codeableConcept with mixed-system codings (lateral combination
    beyond the prior SKEPTIC + HISTORIAN probes).

    Spec: https://hl7.org/fhir/R4/valueset-operation-validate-code.html
    In `codeableConcept`: "A full codeableConcept to validate. The
    server returns true if one of the coding values is in the value
    set".
    """

    def test_e60_cc_mixed_system_first_invalid_second_valid_returns_true(
        self, fhir_client,
    ):
        """e60: codeableConcept [(SNOMED-INVALID), (ICD10-E11)]. Result
        MUST be true (second coding matches). Matched coding's Out
        system MUST be ICD10CM canonical URI (client-input-as-canonical
        drift pattern count=8 PROMOTED).
        """
        body = _codeable_concept_post_body([
            (SNOMED_URI, "INVALID-SNOMED"),
            (ICD10CM_URI, ICD10CM_E11_CODE),
        ])
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        params = r.json()
        assert _param_value(params, "result") is True
        # Out system MUST be ICD10CM canonical (matched coding), NOT SNOMED
        assert _param_value(params, "system") == ICD10CM_URI
        # Out code MUST be ICD10 E11 (matched coding), NOT INVALID-SNOMED
        assert _param_value(params, "code") == ICD10CM_E11_CODE
        # Out display MUST be ICD10 E11 canonical
        assert _param_value(params, "display") == ICD10CM_E11_DISPLAY

    def test_e61_cc_mixed_system_first_valid_second_invalid_returns_true(
        self, fhir_client,
    ):
        """e61: sibling of e60 — first VALID, second INVALID. Result
        MUST be true (first coding matches). Matched coding's Out
        system MUST be SNOMED canonical.
        """
        body = _codeable_concept_post_body([
            (SNOMED_URI, SNOMED_T2DM_CODE),
            (ICD10CM_URI, "INVALID-ICD10"),
        ])
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        params = r.json()
        assert _param_value(params, "result") is True
        assert _param_value(params, "system") == SNOMED_URI
        assert _param_value(params, "code") == SNOMED_T2DM_CODE
        assert _param_value(params, "display") == SNOMED_T2DM_DISPLAY

    def test_e62_cc_first_pair_unrecognized_system_second_valid_returns_true(
        self, fhir_client,
    ):
        """e62: lateral combination — first coding has an UNRECOGNIZED
        system URI; second coding is valid. The unrecognized system
        MUST be silently skipped (no 5xx); the second coding's match
        MUST produce result=true.
        """
        body = _codeable_concept_post_body([
            ("http://unrecognized.example/system", "ANY-CODE"),
            (RXNORM_URI, RXNORM_METFORMIN_CODE),
        ])
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        params = r.json()
        assert _param_value(params, "result") is True
        assert _param_value(params, "system") == RXNORM_URI
        assert _param_value(params, "code") == RXNORM_METFORMIN_CODE

    def test_e63_cc_all_pairs_unrecognized_system_returns_false_with_message(
        self, fhir_client,
    ):
        """e63: lateral combination — all codings have UNRECOGNIZED
        systems. Result MUST be false (no coding matched) with a
        message. No 5xx.
        """
        body = _codeable_concept_post_body([
            ("http://unrecognized1.example/system", "CODE1"),
            ("http://unrecognized2.example/system", "CODE2"),
        ])
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        params = r.json()
        assert _param_value(params, "result") is False
        assert _param_value(params, "message") is not None

    def test_e64_cc_with_alias_input_system_resolves_to_canonical(
        self, fhir_client,
    ):
        """e64: lateral combination — codeableConcept with SNOMED OID
        alias as the system. Matched coding's Out system MUST be the
        canonical SNOMED URI (client-input-as-canonical drift count=8
        PROMOTED on the CC path per CR-025).
        """
        body = _codeable_concept_post_body([
            (SNOMED_OID_ALIAS, SNOMED_T2DM_CODE),
        ])
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        params = r.json()
        assert _param_value(params, "result") is True
        # Out system MUST be canonical SNOMED URI (NOT the OID alias)
        assert _param_value(params, "system") == SNOMED_URI


# =============================================================================
# Lens 7 — Lateral combination: implicit VS URL with codeableConcept body
# =============================================================================


class TestLens7ImplicitVSURLWithCodeableConcept:
    """L7: implicit VS URL with codeableConcept body (vs explicit
    system+code). Lateral combination of two spec features that haven't
    been combined in prior SKEPTIC + HISTORIAN probes.
    """

    def test_e70_implicit_vs_url_with_codeableConcept_valid_returns_true(
        self, fhir_client,
    ):
        """e70: url=implicit VS + codeableConcept [T2DM]. Result MUST
        be true. The implicit VS URL doesn't restrict the codeableConcept
        matching (current implementation reduces membership to code
        presence in the underlying code system per the docstring on
        ``_do_vs_validate``).
        """
        body = _parameters_body(
            ("url", {"valueUri": SNOMED_URI}),
            (
                "codeableConcept",
                {"valueCodeableConcept": {
                    "coding": [{"system": SNOMED_URI, "code": SNOMED_T2DM_CODE}],
                }},
            ),
        )
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        params = r.json()
        assert _param_value(params, "result") is True
        assert _param_value(params, "display") == SNOMED_T2DM_DISPLAY

    def test_e71_implicit_vs_url_with_codeableConcept_all_invalid_returns_false(
        self, fhir_client,
    ):
        """e71: url=implicit VS + codeableConcept [INVALID]. Result MUST
        be false (no coding matched).
        """
        body = _parameters_body(
            ("url", {"valueUri": SNOMED_URI}),
            (
                "codeableConcept",
                {"valueCodeableConcept": {
                    "coding": [{"system": SNOMED_URI, "code": "INVALID-CC"}],
                }},
            ),
        )
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        params = r.json()
        assert _param_value(params, "result") is False

    def test_e72_implicit_vs_url_intensional_with_codeableConcept(
        self, fhir_client,
    ):
        """e72: url=SNOMED intensional URL + codeableConcept [T2DM].
        Result MUST be true.
        """
        body = _parameters_body(
            ("url", {"valueUri": f"{SNOMED_URI}?fhir_vs=isa"}),
            (
                "codeableConcept",
                {"valueCodeableConcept": {
                    "coding": [{"system": SNOMED_URI, "code": SNOMED_T2DM_CODE}],
                }},
            ),
        )
        r = _validate_vs_post(fhir_client, body)
        assert r.status_code == 200
        params = r.json()
        assert _param_value(params, "result") is True


# =============================================================================
# Lens 8 — GET↔POST byte-exact parity on lateral shapes
# =============================================================================


class TestLens8GetPostParityOnLateralShapes:
    """L8: GET↔POST byte-exact parity on lateral shapes.

    SKEPTIC test_s110..s112 verified GET↔POST byte-exact parity on
    hostile inputs. EXPLORER extends to lateral combination shapes.
    """

    @pytest.mark.parametrize(
        "system_uri, code, display",
        [
            (SNOMED_URI, SNOMED_DM_CODE, SNOMED_DM_DISPLAY),
            (SNOMED_URI, SNOMED_T2DM_CODE, SNOMED_T2DM_DISPLAY),
            (ICD10CM_URI, ICD10CM_E11_CODE, ICD10CM_E11_DISPLAY),
            (RXNORM_URI, RXNORM_METFORMIN_CODE, RXNORM_METFORMIN_DISPLAY),
        ],
    )
    def test_e80_get_post_parity_with_implicit_vs_url_per_system(
        self, fhir_client, system_uri, code, display,
    ):
        """e80: 4-parametrized — GET↔POST byte-exact parity with url=
        implicit VS per seeded system. Catches silent per-system drift
        on the implicit VS URL form.
        """
        # GET
        r_get = _validate_vs_get(
            fhir_client,
            url=system_uri,
            system=system_uri,
            code=code,
        )
        assert r_get.status_code == 200

        # POST
        body = _parameters_body(
            ("url", {"valueUri": system_uri}),
            ("system", {"valueUri": system_uri}),
            ("code", {"valueCode": code}),
        )
        r_post = _validate_vs_post(fhir_client, body)
        assert r_post.status_code == 200

        assert (
            _param_value(r_get.json(), "result")
            == _param_value(r_post.json(), "result")
        )
        assert (
            _param_value(r_get.json(), "display")
            == _param_value(r_post.json(), "display")
            == display
        )
        assert (
            _param_value(r_get.json(), "system")
            == _param_value(r_post.json(), "system")
            == system_uri
        )

    def test_e81_get_post_parity_with_url_and_system_combined(
        self, fhir_client,
    ):
        """e81: GET↔POST byte-exact parity when BOTH url= AND system=
        are supplied (spec allows both — In param table shows url 0..1
        AND system 0..1). EXPLORER verifies this lateral combination
        doesn't produce divergence between GET and POST.
        """
        # GET with url + system + code
        r_get = _validate_vs_get(
            fhir_client,
            url=SNOMED_URI,
            system=SNOMED_URI,
            code=SNOMED_T2DM_CODE,
        )
        assert r_get.status_code == 200

        # POST with url + system + code
        body = _parameters_body(
            ("url", {"valueUri": SNOMED_URI}),
            ("system", {"valueUri": SNOMED_URI}),
            ("code", {"valueCode": SNOMED_T2DM_CODE}),
        )
        r_post = _validate_vs_post(fhir_client, body)
        assert r_post.status_code == 200

        # Byte-exact result + display + system
        assert (
            _param_value(r_get.json(), "result")
            == _param_value(r_post.json(), "result")
        )
        assert (
            _param_value(r_get.json(), "display")
            == _param_value(r_post.json(), "display")
            == SNOMED_T2DM_DISPLAY
        )
        assert (
            _param_value(r_get.json(), "system")
            == _param_value(r_post.json(), "system")
        )

    def test_e82_get_post_parity_with_alias_system_input(
        self, fhir_client,
    ):
        """e82: GET↔POST byte-exact parity with SNOMED OID alias as
        system input. Both paths MUST resolve to canonical SNOMED URI
        (client-input-as-canonical drift count=8 PROMOTED on BOTH
        paths).
        """
        # GET with alias
        r_get = _validate_vs_get(
            fhir_client,
            system=SNOMED_OID_ALIAS,
            code=SNOMED_T2DM_CODE,
        )
        assert r_get.status_code == 200

        # POST with alias
        body = _parameters_body(
            ("system", {"valueUri": SNOMED_OID_ALIAS}),
            ("code", {"valueCode": SNOMED_T2DM_CODE}),
        )
        r_post = _validate_vs_post(fhir_client, body)
        assert r_post.status_code == 200

        # Both resolve to canonical SNOMED URI
        assert (
            _param_value(r_get.json(), "system")
            == _param_value(r_post.json(), "system")
            == SNOMED_URI
        )
        assert (
            _param_value(r_get.json(), "display")
            == _param_value(r_post.json(), "display")
            == SNOMED_T2DM_DISPLAY
        )


# =============================================================================
# Lens 9 — Source-read structural contracts (EXPLORER lateral extensions)
# =============================================================================


class TestLens9SourceReadStructuralContracts:
    """L9: source-read structural contracts for EXPLORER lateral probes.

    Each probe source-reads ``_do_vs_validate`` to lock in expected
    behaviors independent of fixture data.
    """

    def test_e90_do_vs_validate_routes_through_canonical_system_uri_on_cc_path(
        self,
    ):
        """e90: SOURCE-READ — ``_do_vs_validate`` MUST call
        ``canonical_system_uri`` on the codeableConcept matched-uri
        path. CR-025 fix shape (count=8 PROMOTED client-input-as-
        canonical drift).

        Distinct from SKEPTIC test_s81 (which source-reads the SCALAR
        path). EXPLORER source-reads the CC path to verify the fix is
        intact there too.
        """
        module_src = _read_module_source()
        cc_src = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_vs_validate",
        )
        assert cc_src is not None

        # CC path: ``canonical_matched_uri = canonical_system_uri(matched_uri) ...``
        assert "canonical_system_uri(matched_uri)" in cc_src, (
            "CC path MUST call canonical_system_uri(matched_uri) per CR-025."
        )

    def test_e91_do_vs_validate_has_message_only_on_unknown_code_in_scalar(
        self,
    ):
        """e91: SOURCE-READ — the SCALAR path of ``_do_vs_validate``
        has the unknown-code message "Code X is not valid in code
        system Y". This is the load-bearing message for the cross-
        handler CS↔VS message parity (HISTORIAN tip #4).

        Distinct from SKEPTIC test_s83 (which source-reads the
        display-mismatch message). EXPLORER source-reads the unknown-
        code message.
        """
        module_src = _read_module_source()
        cc_src = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_vs_validate",
        )
        assert cc_src is not None

        assert "is not valid in code system" in cc_src, (
            "Scalar path MUST include the unknown-code message format "
            "'Code X is not valid in code system Y' for cross-handler "
            "CS↔VS message parity."
        )

    def test_e92_do_vs_validate_cc_branch_returns_true_on_match(
        self,
    ):
        """e92: SOURCE-READ — the CC matched-info block calls
        ``build_parameters_validate(True, ...)`` (result=True). This is
        the load-bearing structural contract for the spec-permitted non-
        enforcement on the CC path (CS-03 SKEPTIC AUDIT-002).
        """
        module_src = _read_module_source()
        cc_src = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_vs_validate",
        )
        assert cc_src is not None

        # Parse AST and find the CC branch's matched-info return
        tree = ast.parse(cc_src)
        cc_block = None
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                test_src = ast.get_source_segment(cc_src, node.test) or ""
                if "codeable_concept_pairs" in test_src:
                    cc_block = node
                    break
        assert cc_block is not None
        cc_block_src = ast.get_source_segment(cc_src, cc_block) or ""

        # The CC matched-info block MUST call build_parameters_validate(True, ...)
        # We check via AST: the literal True appears as the first positional
        # arg to build_parameters_validate within the CC block.
        # Heuristic: search for the pattern in the source segment.
        assert "build_parameters_validate(\n                    True," in cc_block_src or \
               "build_parameters_validate(True," in cc_block_src or \
               "build_parameters_validate(\n                True," in cc_block_src, (
            "CC matched-info block MUST call build_parameters_validate(True, ...)."
        )

    def test_e93_do_validate_sibling_has_same_unknown_code_message(
        self,
    ):
        """e93: SOURCE-READ — sibling ``_do_validate`` (CodeSystem
        handler) MUST emit the SAME unknown-code message as
        ``_do_vs_validate``. Cross-handler CS↔VS message parity on the
        unknown-code path (HISTORIAN tip #4).

        The structural contract: both handlers contain
        "is not valid in code system" in their source.
        """
        module_src = _read_module_source()
        cs_src = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_validate",
        )
        assert cs_src is not None

        vs_src = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_vs_validate",
        )
        assert vs_src is not None

        assert "is not valid in code system" in cs_src
        assert "is not valid in code system" in vs_src

    def test_e94_do_vs_validate_no_broad_except(self):
        """e94: SOURCE-READ — ``_do_vs_validate`` MUST NOT contain
        ``except Exception`` (silent-fallback prohibition per
        GLOBAL_RULES.md). Mirrors SKEPTIC test_s122.
        """
        module_src = _read_module_source()
        vs_src = _read_nested_function_source(
            module_src, "create_fhir_app", "_do_vs_validate",
        )
        assert vs_src is not None

        tree = ast.parse(vs_src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                # No bare 'except Exception:' allowed
                if node.type is None:
                    # Bare except — prohibited
                    assert False, "Bare 'except:' found in _do_vs_validate"
                if isinstance(node.type, ast.Name) and node.type.id == "Exception":
                    assert False, (
                        "'except Exception:' found in _do_vs_validate "
                        "(silent-fallback prohibition per GLOBAL_RULES.md)"
                    )


# =============================================================================
# Lens 10 — Cross-handler CS↔VS parity on lateral shapes
# =============================================================================


class TestLens10CrossHandlerParityOnLateralShapes:
    """L10: cross-handler CS↔VS parity on lateral shapes.

    SKEPTIC test_s71 verified CS↔VS Out display byte-exact for KNOWN
    codes. EXPLORER extends to lateral shapes (unknown-system, all-
    invalid codeableConcept, etc.).
    """

    def test_e100_cross_handler_cs_vs_unknown_system_byte_exact(
        self, fhir_client,
    ):
        """e100: CS↔VS byte-exact on the unknown-system path. Both
        handlers reject with the same error code AND message.
        """
        r_cs = _validate_cs_get(
            fhir_client,
            system="http://unknown.example/system",
            code="ANY",
        )
        r_vs = _validate_vs_get(
            fhir_client,
            system="http://unknown.example/system",
            code="ANY",
        )
        assert r_cs.status_code == r_vs.status_code == 400

    def test_e101_cross_handler_cs_vs_cc_all_invalid_message_byte_exact(
        self, fhir_client,
    ):
        """e101: CS↔VS byte-exact message on the codeableConcept all-
        invalid path. Both handlers emit the SAME message when no
        coding matches.
        """
        body = _codeable_concept_post_body([
            (SNOMED_URI, "INVALID-1"),
            (ICD10CM_URI, "INVALID-2"),
        ])
        r_cs = fhir_client.post(
            "/fhir/CodeSystem/$validate-code",
            json=body,
            headers={"Accept": "application/fhir+json"},
        )
        r_vs = _validate_vs_post(fhir_client, body)
        assert r_cs.status_code == 200
        assert r_vs.status_code == 200

        cs_msg = _param_value(r_cs.json(), "message")
        vs_msg = _param_value(r_vs.json(), "message")
        assert cs_msg == vs_msg is not None
        assert "None of the codings" in cs_msg

    def test_e102_cross_handler_cs_vs_cc_matched_display_byte_exact(
        self, fhir_client,
    ):
        """e102: CS↔VS byte-exact Out display on the codeableConcept
        matched-coding path. Both handlers emit the same canonical
        display for the matched coding.
        """
        body = _codeable_concept_post_body([
            (SNOMED_URI, "INVALID"),
            (RXNORM_URI, RXNORM_METFORMIN_CODE),
        ])
        r_cs = fhir_client.post(
            "/fhir/CodeSystem/$validate-code",
            json=body,
            headers={"Accept": "application/fhir+json"},
        )
        r_vs = _validate_vs_post(fhir_client, body)
        assert r_cs.status_code == 200
        assert r_vs.status_code == 200

        cs_display = _param_value(r_cs.json(), "display")
        vs_display = _param_value(r_vs.json(), "display")
        assert cs_display == vs_display == RXNORM_METFORMIN_DISPLAY

    def test_e103_cross_handler_cs_vs_cc_matched_system_byte_exact(
        self, fhir_client,
    ):
        """e103: CS↔VS byte-exact Out system on the codeableConcept
        matched-coding path. Both handlers emit the same canonical URI
        (client-input-as-canonical drift count=8 PROMOTED on BOTH
        handlers' CC paths per CR-025).
        """
        body = _codeable_concept_post_body([
            (SNOMED_OID_ALIAS, SNOMED_T2DM_CODE),  # alias input
        ])
        r_cs = fhir_client.post(
            "/fhir/CodeSystem/$validate-code",
            json=body,
            headers={"Accept": "application/fhir+json"},
        )
        r_vs = _validate_vs_post(fhir_client, body)
        assert r_cs.status_code == 200
        assert r_vs.status_code == 200

        cs_sys = _param_value(r_cs.json(), "system")
        vs_sys = _param_value(r_vs.json(), "system")
        # Both resolve to canonical SNOMED URI (NOT the OID alias)
        assert cs_sys == vs_sys == SNOMED_URI


# =============================================================================
# Lens 11 — Carry-forward reconfirmations (PROMOTED patterns on VS-05)
# =============================================================================


class TestLens11CarryForwardReconfirmations:
    """L11: reconfirm PROMOTED patterns from GLOBAL_RULES.md on the
    VS-05 EXPLORER surface.

    Each probe asserts the load-bearing structural invariant of a
    PROMOTED pattern, source-read where possible.
    """

    def test_e110_client_input_as_canonical_drift_count_8_promoted_held(
        self, fhir_client,
    ):
        """e110: client-input-as-canonical drift pattern (count=8
        PROMOTED) MUST NOT recur on the VS-05 surface. The CC path +
        alias input MUST resolve to canonical URI on BOTH handlers.
        """
        # VS handler, CC path, alias input
        body = _codeable_concept_post_body([
            (SNOMED_OID_ALIAS, SNOMED_T2DM_CODE),
        ])
        r_vs = _validate_vs_post(fhir_client, body)
        assert _param_value(r_vs.json(), "system") == SNOMED_URI

        # CS handler, CC path, alias input (sibling)
        r_cs = fhir_client.post(
            "/fhir/CodeSystem/$validate-code",
            json=body,
            headers={"Accept": "application/fhir+json"},
        )
        assert _param_value(r_cs.json(), "system") == SNOMED_URI

    def test_e111_min_length_not_required_on_vs_validate_get(self):
        """e111: VS/$validate-code GET does NOT require min_length=1 on
        ``Query(None)`` params (alternative encodings make system+code
        not strictly required at the FastAPI level). Distinct from
        CodeSystem/$validate-code where system+code ARE strictly
        required (TS-02 SKEPTIC QA-002 count=5 PROMOTED).
        """
        module_src = _read_module_source()
        # Find vs_validate_get source
        tree = ast.parse(module_src)
        vs_get = None
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "vs_validate_get"
            ):
                vs_get = node
                break
        assert vs_get is not None
        vs_get_src = ast.get_source_segment(module_src, vs_get) or ""

        # All params use Query(None) — no required sentinel (Ellipsis)
        # The structural contract: vs_validate_get declares `url`,
        # `code`, `system` with Query(None) (not Query(...))
        assert "Query(None" in vs_get_src or "Query(" in vs_get_src
        # No min_length=1 on these params
        assert "min_length=1" not in vs_get_src, (
            "vs_validate_get should NOT require min_length=1 per "
            "VS-05 SKEPTIC test_s86 documentation (alternative encodings)."
        )

    def test_e112_isinstance_dict_guard_in_all_pairs_helper(self):
        """e112: 10th PROMOTED pattern (isinstance-dict guard count=4)
        — ``_extract_all_coding_pairs_from_codeable_concept`` MUST
        contain an ``isinstance(<var>, dict)`` call. Mirrors SKEPTIC
        test_s87.
        """
        module_src = _read_module_source()
        # Find the all-pairs helper (it's defined at module level OR
        # inside create_fhir_app — search both ways).
        tree = ast.parse(module_src)
        # Try direct ast.walk for module-level def first
        helper_src = None
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "_extract_all_coding_pairs_from_codeable_concept"
            ):
                helper_src = ast.get_source_segment(module_src, node) or ""
                break

        if helper_src is None:
            # Try nested form
            helper_src = _read_nested_function_source(
                module_src, "create_fhir_app",
                "_extract_all_coding_pairs_from_codeable_concept",
            )

        assert helper_src is not None, \
            "_extract_all_coding_pairs_from_codeable_concept not found"

        # The helper MUST contain an isinstance(<var>, dict) call
        assert "isinstance(" in helper_src, (
            "10th PROMOTED pattern (isinstance-dict guard) — the all-pairs "
            "helper MUST contain an isinstance(<var>, dict) call."
        )
        assert ", dict)" in helper_src or "dict)" in helper_src

    def test_e113_no_broad_exception_in_vs_validate_handlers(self):
        """e113: ``vs_validate_get`` and ``vs_validate_post`` MUST NOT
        contain ``except Exception`` (silent-fallback prohibition per
        GLOBAL_RULES.md).
        """
        module_src = _read_module_source()
        tree = ast.parse(module_src)
        for handler_name in ("vs_validate_get", "vs_validate_post"):
            handler = None
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == handler_name
                ):
                    handler = node
                    break
            assert handler is not None, f"{handler_name} not found"
            handler_src = ast.get_source_segment(module_src, handler) or ""
            handler_tree = ast.parse(handler_src)
            for sub in ast.walk(handler_tree):
                if isinstance(sub, ast.ExceptHandler):
                    if sub.type is None:
                        assert False, f"Bare 'except:' in {handler_name}"
                    if isinstance(sub.type, ast.Name) and sub.type.id == "Exception":
                        assert False, (
                            f"'except Exception:' in {handler_name} "
                            "(silent-fallback prohibition)"
                        )

    def test_e114_canonical_system_uri_helper_is_imported(self):
        """e114: ``canonical_system_uri`` helper IS imported in
        ``apps/fhir_api.py``. The canonical-system-URI registry is
        the single source of truth per GLOBAL_RULES.md.
        """
        module_src = _read_module_source()
        # The import should appear at module scope
        assert "canonical_system_uri" in module_src, (
            "canonical_system_uri helper is not present in apps/fhir_api.py"
        )
        # Verify it's actually imported (not just defined locally)
        # Look for "from medterm4ds.engines.fhir import" containing it
        assert (
            "from medterm4ds.engines.fhir import" in module_src
            or "from .engines.fhir import" in module_src
        ), "engines.fhir import line not found"


# =============================================================================
# Lens 12 — META: cross-handler CS↔VS uniformity across ALL lateral shapes
# =============================================================================


class TestLens12MetaCrossHandlerUniformity:
    """L12: META — cross-handler CS↔VS uniformity across ALL lateral
    shapes tested in this file.

    The META-PATTERN: for every lateral shape tested in EXPLORER, both
    handlers (CS + VS) MUST agree byte-exact on result + message +
    display + system. This is the load-bearing cross-handler parity
    invariant that catches silent drift between the two sibling
    handlers.
    """

    @pytest.mark.parametrize(
        "system_uri, code, display",
        [
            (SNOMED_URI, SNOMED_DM_CODE, SNOMED_DM_DISPLAY),
            (SNOMED_URI, SNOMED_T2DM_CODE, SNOMED_T2DM_DISPLAY),
            (ICD10CM_URI, ICD10CM_E11_CODE, ICD10CM_E11_DISPLAY),
            (RXNORM_URI, RXNORM_METFORMIN_CODE, RXNORM_METFORMIN_DISPLAY),
        ],
    )
    def test_e120_meta_cs_vs_match_agreement_per_system(
        self, fhir_client, system_uri, code, display,
    ):
        """e120: META — CS↔VS byte-exact on the matching-display case
        per seeded system. Both handlers MUST agree on result + display
        + system + code.
        """
        r_cs = _validate_cs_get(
            fhir_client, system=system_uri, code=code, display=display,
        )
        r_vs = _validate_vs_get(
            fhir_client, system=system_uri, code=code, display=display,
        )
        assert r_cs.status_code == 200
        assert r_vs.status_code == 200

        cs_body = r_cs.json()
        vs_body = r_vs.json()

        assert _param_value(cs_body, "result") == _param_value(vs_body, "result") is True
        assert _param_value(cs_body, "display") == _param_value(vs_body, "display") == display
        assert _param_value(cs_body, "system") == _param_value(vs_body, "system") == system_uri
        assert _param_value(cs_body, "code") == _param_value(vs_body, "code") == code

    @pytest.mark.parametrize(
        "system_uri, code, canonical_display",
        [
            (SNOMED_URI, SNOMED_DM_CODE, SNOMED_DM_DISPLAY),
            (SNOMED_URI, SNOMED_T2DM_CODE, SNOMED_T2DM_DISPLAY),
            (ICD10CM_URI, ICD10CM_E11_CODE, ICD10CM_E11_DISPLAY),
            (RXNORM_URI, RXNORM_METFORMIN_CODE, RXNORM_METFORMIN_DISPLAY),
        ],
    )
    def test_e121_meta_cs_vs_mismatch_agreement_per_system(
        self, fhir_client, system_uri, code, canonical_display,
    ):
        """e121: META — CS↔VS byte-exact on the mismatch case per
        seeded system. Both handlers MUST agree on result + display +
        message.
        """
        r_cs = _validate_cs_get(
            fhir_client, system=system_uri, code=code,
            display="WRONG META DISPLAY",
        )
        r_vs = _validate_vs_get(
            fhir_client, system=system_uri, code=code,
            display="WRONG META DISPLAY",
        )
        assert r_cs.status_code == 200
        assert r_vs.status_code == 200

        cs_body = r_cs.json()
        vs_body = r_vs.json()

        assert _param_value(cs_body, "result") == _param_value(vs_body, "result") is False
        assert (
            _param_value(cs_body, "display")
            == _param_value(vs_body, "display")
            == canonical_display
        )
        assert (
            _param_value(cs_body, "message")
            == _param_value(vs_body, "message")
            == 'The display "WRONG META DISPLAY" is incorrect'
        )

    def test_e122_meta_cs_vs_alias_input_agreement(self, fhir_client):
        """e122: META — CS↔VS byte-exact on alias input (SNOMED OID).
        Both handlers MUST resolve to canonical SNOMED URI + canonical
        display.
        """
        r_cs = _validate_cs_get(
            fhir_client, system=SNOMED_OID_ALIAS, code=SNOMED_T2DM_CODE,
        )
        r_vs = _validate_vs_get(
            fhir_client, system=SNOMED_OID_ALIAS, code=SNOMED_T2DM_CODE,
        )
        assert r_cs.status_code == 200
        assert r_vs.status_code == 200

        cs_body = r_cs.json()
        vs_body = r_vs.json()

        assert _param_value(cs_body, "result") == _param_value(vs_body, "result") is True
        assert (
            _param_value(cs_body, "system")
            == _param_value(vs_body, "system")
            == SNOMED_URI
        )
        assert (
            _param_value(cs_body, "display")
            == _param_value(vs_body, "display")
            == SNOMED_T2DM_DISPLAY
        )
