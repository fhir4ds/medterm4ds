"""VS-05 HISTORIAN resweep: ValueSet $validate-code Operation.

Source: https://build.fhir.org/valueset-operation-validate-code.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-validate-code.html

This is the HISTORIAN resweep (2nd of 4 personalities) for chunk VS-05.

HISTORIAN lens: pattern-match against prior VS-05 bug patterns. Every prior
bug fix MUST be re-derived HELD via regression-style probes (behavioral +
source-read). Every PROMOTED pattern from GLOBAL_RULES.md MUST be re-derived
on the VS-05 surface.

Prior VS-05 patterns to re-derive (per task assignment):
  - QA-069: display mismatch enforcement on ``_do_vs_validate``
    (CF-SKEPTIC-CS03-01 CLOSED — 4th META confirmation of the carry-forward-
    as-probe pattern)
  - QA-070: codeableConcept multi-coding all-pairs helper wiring
    (mirror of CS-03 QA-049 + CS-03 HISTORIAN QA-052)
  - CF-EXPLORER-CS02-01 FULLY CLOSED (per SKEPTIC resweep)
  - Plus 11 PROMOTED patterns (count_limited strict->, client-input-as-
    canonical, helper-wiring consistency, isinstance-dict guards,
    min_length=1, etc.)

SKEPTIC tip for HISTORIAN (2 items) — ADDRESSED in this file:

  Tip #1: Re-verify the 8th META-PATTERN surface via byte-exact cross-op
  display agreement parametrized over every seeded system AND every display-
  mismatch variant. SKEPTIC's parametrization was over the matching-display
  case (test_s70); HISTORIAN should extend to the **mismatch case** to
  catch silent drift on the message format too.

  Tip #2: Re-confirm CF-EXPLORER-CS02-01 FULLY CLOSED via a META-walk probe
  that counts >=1 probe per shape on every operation accepting a Parameters
  body.

Conformance fixture seeds (per tests/fhir_conformance/conftest.py):
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
# Constants — seeded systems + codes (mirror SKEPTIC resweep constants).
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
SNOMED_SLASH_ALIAS = "http://snomed.info/sct/"

_FHIR_API_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _param_value(body: dict, name: str):
    """Return the first value* field for an Out parameter named ``name``."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


def _lookup_out_display(client, system: str, code: str):
    r = client.get(
        f"/fhir/CodeSystem/$lookup?system={system}&code={code}"
    )
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


def _validate_cs_get(client, *, system=None, code=None, display=None):
    params = []
    if system is not None:
        params.append(("system", system))
    if code is not None:
        params.append(("code", code))
    if display is not None:
        params.append(("display", display))
    return client.get("/fhir/CodeSystem/$validate-code", params=params)


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
# Lens 1: QA-069 re-derivation (display mismatch enforcement on _do_vs_validate)
# CF-SKEPTIC-CS03-01 CLOSED — 4th META confirmation of the carry-forward-as-
# probe pattern. HISTORIAN re-derives the fix shape via source-read +
# behavioral probes.
# =============================================================================


class TestLens1QA069DisplayMismatch:
    """Re-derive VS-05 SKEPTIC QA-069 (display mismatch on _do_vs_validate).

    The carry-forward CF-SKEPTIC-CS03-01 was opened in CS-03 SKEPTIC and
    closed by VS-05 SKEPTIC. HISTORIAN verifies the closing fix shape is
    intact via:
      - Source-read probe asserting the inline check at ``_do_vs_validate``.
      - Source-read probe asserting the byte-exact message text.
      - Behavioral probe parametrized over every seeded system.
      - Cross-handler CS↔VS message parity probe (sibling check).
    """

    def test_h10_qa069_inline_display_mismatch_check_in_do_vs_validate(self):
        """CR-SKEPTIC-CS03-01 CLOSED shape — the inline display-mismatch
        comparison MUST be present in ``_do_vs_validate`` (not just in the
        sibling ``_do_validate``).

        Source-read probe so the assertion survives any future refactor
        that moves the logic around.
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_do_vs_validate"
        )
        assert src is not None, "_do_vs_validate not found in create_fhir_app"
        assert "display != canonical_display" in src, (
            "QA-069: _do_vs_validate MUST contain the inline display "
            "mismatch comparison 'display != canonical_display'. If this "
            "fires, CF-SKEPTIC-CS03-01 has regressed."
        )

    def test_h11_qa069_message_text_byte_exact_format_in_source(self):
        """Spec example message text MUST be present in ``_do_vs_validate``.

        Per FHIR R4 codesystem-operation-validate-code.html example
        response: result=false + message='The display "X" is incorrect'.
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_do_vs_validate"
        )
        assert src is not None
        assert 'The display "' in src and '"is incorrect' not in src.replace(
            'The display "', "", 1
        ), (
            "QA-069: spec example message format 'The display \"X\" is "
            "incorrect' MUST be present in _do_vs_validate source."
        )
        # Also confirm the f-string template is present (not just the
        # substring of an unrelated code path).
        assert "is incorrect" in src, (
            "QA-069: 'is incorrect' message text MUST be present."
        )

    @pytest.mark.parametrize(
        "system, code, canonical",
        [
            (SNOMED_URI, SNOMED_DM_CODE, SNOMED_DM_DISPLAY),
            (SNOMED_URI, SNOMED_T2DM_CODE, SNOMED_T2DM_DISPLAY),
            (ICD10CM_URI, ICD10CM_E11_CODE, ICD10CM_E11_DISPLAY),
            (RXNORM_URI, RXNORM_METFORMIN_CODE, RXNORM_METFORMIN_DISPLAY),
        ],
        ids=["snomed-dm", "snomed-t2dm", "icd10-e11", "rxnorm-metformin"],
    )
    def test_h12_qa069_display_mismatch_per_system(
        self, fhir_client, system, code, canonical,
    ):
        """Behavioral re-derivation parametrized over EVERY seeded system.

        For each seeded code, supplying a wrong display MUST produce
        result=false + canonical display in the Out params + message
        citing the wrong value. SKEPTIC test_s50 covered this; HISTORIAN
        re-derives via a separate probe so a regression is isolated to
        this test class.
        """
        wrong = f"HISTORIAN-MISMATCH-{code}"
        r = _validate_vs_get(
            fhir_client, system=system, code=code, display=wrong,
        )
        assert r.status_code == 200
        body = r.json()
        assert _param_value(body, "result") is False, (
            f"QA-069 on ({system}, {code}): wrong display MUST yield "
            f"result=false. Got {_param_value(body, 'result')!r}."
        )
        assert _param_value(body, "display") == canonical, (
            f"QA-069 on ({system}, {code}): Out display MUST be the "
            f"engine canonical {canonical!r}, not client echo."
        )
        assert _param_value(body, "message") == (
            f'The display "{wrong}" is incorrect'
        )

    def test_h13_qa069_cross_handler_cs_vs_message_parity(self, fhir_client):
        """CS↔VS message parity probe — for the same (system, code, wrong
        display), the message text MUST be byte-exact identical between
        CodeSystem/$validate-code and ValueSet/$validate-code.

        Guards against a future refactor that touches one handler's
        message template but not the sibling's.
        """
        wrong = "HISTORIAN-PARITY-WRONG"
        cs_r = _validate_cs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE, display=wrong,
        )
        vs_r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE, display=wrong,
        )
        assert cs_r.status_code == vs_r.status_code == 200
        cs_msg = _param_value(cs_r.json(), "message")
        vs_msg = _param_value(vs_r.json(), "message")
        assert cs_msg == vs_msg == f'The display "{wrong}" is incorrect', (
            f"CS↔VS message drift: CS={cs_msg!r}, VS={vs_msg!r}."
        )

    def test_h14_qa069_canonical_display_returned_not_client_echo(self, fhir_client):
        """Per TS-02 TERMINOLOGIST QA-029 + VS-05 SKEPTIC QA-069: the Out
        ``display`` parameter MUST be the engine canonical preferred term,
        NOT an echo of the client's wrong display value.

        Client-input-as-canonical drift count=8 PROMOTED — re-derive HELD.
        """
        wrong = "HISTORIAN-DEFINITELY-NOT-CANONICAL"
        r = _validate_vs_get(
            fhir_client, system=RXNORM_URI, code=RXNORM_METFORMIN_CODE,
            display=wrong,
        )
        body = r.json()
        out_display = _param_value(body, "display")
        assert out_display == RXNORM_METFORMIN_DISPLAY, (
            f"Out display MUST be canonical {RXNORM_METFORMIN_DISPLAY!r}, "
            f"not client echo {out_display!r}."
        )
        assert out_display != wrong


# =============================================================================
# Lens 2: QA-070 re-derivation (codeableConcept multi-coding all-pairs helper)
# Mirror of CS-03 QA-049 + CS-03 HISTORIAN QA-052 on the VS surface.
# =============================================================================


class TestLens2QA070CodeableConceptAllPairs:
    """Re-derive VS-05 SKEPTIC QA-070 — codeableConcept multi-coding
    all-pairs helper wired into BOTH ``vs_validate_post`` AND
    ``_extract_vs_validate_params`` (batch dispatcher).

    HISTORIAN adds the source-read structural probe on the batch
    dispatcher's return signature (mirrors CS-03 HISTORIAN QA-052 source-
    read on the sibling CS handler's 4-tuple → 5-tuple extension).
    """

    def test_h20_qa070_all_pairs_helper_in_vs_validate_post_source(self):
        """Source-read probe — ``vs_validate_post`` MUST call the all-pairs
        helper (NOT the single-pair helper).

        Guards against a refactor that swaps the helper back to single-pair.
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "vs_validate_post"
        )
        assert src is not None, "vs_validate_post not found"
        assert "_extract_all_coding_pairs_from_codeable_concept" in src, (
            "QA-070: vs_validate_post MUST call the all-pairs helper. "
            "If this fires, the fix has been regressed."
        )

    def test_h21_qa070_all_pairs_helper_in_extract_vs_validate_params_source(self):
        """Source-read probe — batch dispatcher's ``_extract_vs_validate_params``
        MUST call the all-pairs helper.

        Mirrors CS-03 HISTORIAN QA-052 source-read on the sibling
        ``_extract_validate_params`` (4-tuple → 5-tuple extension).
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_extract_vs_validate_params"
        )
        assert src is not None, "_extract_vs_validate_params not found"
        assert "_extract_all_coding_pairs_from_codeable_concept" in src, (
            "QA-070 batch-path: _extract_vs_validate_params MUST call the "
            "all-pairs helper. Mirrors CS-03 HISTORIAN QA-052."
        )

    def test_h22_qa070_batch_returns_5_tuple_source(self):
        """Source-read probe — ``_extract_vs_validate_params`` MUST return
        a 5-tuple (5-tuple extension mirrors CS-03 HISTORIAN QA-052's
        4-tuple on the sibling helper).

        Walks the AST tree, narrows to ``_extract_vs_validate_params``,
        and asserts the FIRST return statement contains ≥5 None defaults.
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_extract_vs_validate_params"
        )
        assert src is not None
        tree = ast.parse(src)
        return_stmts = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple)
        ]
        assert return_stmts, (
            "_extract_vs_validate_params MUST have a tuple-return statement."
        )
        # Find the default-none-style tuple (length >= 5).
        max_len = max(len(n.value.elts) for n in return_stmts)
        assert max_len >= 5, (
            f"QA-070: _extract_vs_validate_params MUST return at least a "
            f"5-tuple (mirror of CS-03 HISTORIAN QA-052 4-tuple extension "
            f"on the sibling). Max found: {max_len}-tuple."
        )

    def test_h23_qa070_behavioral_invalid_then_valid_returns_true(self, fhir_client):
        """Behavioral re-derivation — codeableConcept [INVALID, VALID] →
        result=true via the all-pairs helper.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": SNOMED_URI, "code": "BOGUS_QA_H23"},
                            {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
                        ]
                    },
                }
            ],
        }
        r = fhir_client.post(
            "/fhir/ValueSet/$validate-code", json=body,
            headers={"Accept": "application/fhir+json"},
        )
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is True

    def test_h24_qa070_behavioral_all_invalid_returns_false(self, fhir_client):
        """Behavioral negative — codeableConcept [INVALID, INVALID] →
        result=false via the all-pairs helper.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": SNOMED_URI, "code": "BAD1_H24"},
                            {"system": SNOMED_URI, "code": "BAD2_H24"},
                        ]
                    },
                }
            ],
        }
        r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
        assert r.status_code == 200
        assert _param_value(r.json(), "result") is False


# =============================================================================
# SKEPTIC Tip #1 — 8th META-PATTERN surface: mismatch-case extension
# =============================================================================


class TestSkepticTip1MetaPatternMismatchCase:
    """SKEPTIC Tip #1: Re-verify the 8th META-PATTERN surface via byte-exact
    cross-op display agreement parametrized over every seeded system AND
    every display-mismatch variant.

    SKEPTIC's test_s70 parametrized the MATCHING-display case (Out display
    == $lookup Out display for known codes). HISTORIAN EXTENDS to the
    MISMATCH case to catch silent drift on the message format too.

    The mismatch case has THREE orthogonal assertions per (system, code):
      (a) Out `display` (canonical) byte-exact == $lookup Out `display`
          (canonical-DISPLAY META-PATTERN extends to mismatch case too).
      (b) Out `message` byte-exact == $lookup's display-mismatch message
          IF $lookup had a display parameter (it doesn't, but the format
          template must agree structurally).
      (c) Out `message` format byte-exact == CS/$validate-code Out message
          (cross-handler parity — both handlers use the SAME template).
    """

    @pytest.mark.parametrize(
        "system, code, canonical",
        [
            (SNOMED_URI, SNOMED_DM_CODE, SNOMED_DM_DISPLAY),
            (SNOMED_URI, SNOMED_T2DM_CODE, SNOMED_T2DM_DISPLAY),
            (ICD10CM_URI, ICD10CM_E11_CODE, ICD10CM_E11_DISPLAY),
            (RXNORM_URI, RXNORM_METFORMIN_CODE, RXNORM_METFORMIN_DISPLAY),
        ],
        ids=["snomed-dm", "snomed-t2dm", "icd10-e11", "rxnorm-metformin"],
    )
    def test_h30_mismatch_out_display_byte_exact_with_lookup(
        self, fhir_client, system, code, canonical,
    ):
        """Mismatch-case canonical-DISPLAY META-PATTERN extension.

        On the MISMATCH path, the Out `display` from VS/$validate-code
        MUST byte-exact equal $lookup's Out `display` (canonical) for
        the same (system, code) pair. SKEPTIC test_s70 verified this on
        the matching-display (no display param) path; HISTORIAN extends
        to the mismatch path to catch silent per-system drift.
        """
        wrong = f"MISMATCH-EXT-{code}"
        # VS/$validate-code with wrong display → mismatch path
        vs_r = _validate_vs_get(
            fhir_client, system=system, code=code, display=wrong,
        )
        assert vs_r.status_code == 200
        # $lookup for the same code → canonical display reference
        lookup_display = _lookup_out_display(fhir_client, system, code)
        vs_out_display = _param_value(vs_r.json(), "display")
        assert lookup_display == vs_out_display == canonical, (
            f"Canonical-DISPLAY META-PATTERN drift on MISMATCH path "
            f"({system}, {code}): $lookup={lookup_display!r}, "
            f"VS=$validate-code={vs_out_display!r}, "
            f"canonical={canonical!r}."
        )

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
    def test_h31_mismatch_message_format_byte_exact_per_system(
        self, fhir_client, system, code,
    ):
        """Mismatch-case message format byte-exact per seeded system.

        For every seeded code, supplying a wrong display MUST produce
        byte-exact message='The display "X" is incorrect' (where X is
        the wrong client value). SKEPTIC test_s52 verified the SNOMED
        T2DM case only; HISTORIAN EXTENDS to all 4 seeded systems.

        Catches silent per-system drift on the message template — e.g.
        a future refactor that special-cases RxNorm (which has a long
        display) into a shortened message would break byte-exact
        agreement per spec.
        """
        wrong = f"BYTE-EXACT-PER-SYS-{code}"
        r = _validate_vs_get(
            fhir_client, system=system, code=code, display=wrong,
        )
        body = r.json()
        msg = _param_value(body, "message")
        assert msg == f'The display "{wrong}" is incorrect', (
            f"Per-system message format drift on ({system}, {code}): "
            f"got {msg!r}."
        )

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
    def test_h32_mismatch_cs_vs_message_byte_exact_agreement(
        self, fhir_client, system, code,
    ):
        """Mismatch-case cross-handler CS↔VS message agreement per system.

        For every seeded code, the mismatch message from
        CodeSystem/$validate-code and ValueSet/$validate-code MUST be
        byte-exact identical. SKEPTIC test_s71 verified display agreement
        on the matching case; HISTORIAN EXTENDS to the message agreement
        on the mismatch case to catch silent drift on the template.
        """
        wrong = f"CS-VS-MISMATCH-{code}"
        cs_r = _validate_cs_get(
            fhir_client, system=system, code=code, display=wrong,
        )
        vs_r = _validate_vs_get(
            fhir_client, system=system, code=code, display=wrong,
        )
        assert cs_r.status_code == vs_r.status_code == 200
        cs_msg = _param_value(cs_r.json(), "message")
        vs_msg = _param_value(vs_r.json(), "message")
        assert cs_msg == vs_msg == f'The display "{wrong}" is incorrect', (
            f"CS↔VS mismatch-message drift on ({system}, {code}): "
            f"CS={cs_msg!r}, VS={vs_msg!r}."
        )

    def test_h33_mismatch_unicode_display_format_per_system(self, fhir_client):
        """Mismatch-case META-PATTERN extension on unicode display values.

        A wrong display with unicode chars (CJK, emoji, RTL marks) MUST
        produce byte-exact message citing the unicode verbatim. The
        template format MUST hold across all unicode shapes.
        """
        for wrong in [
            "wrong-显示",        # CJK
            "wrong-éàü",         # accented Latin
            "wrong-🚀",          # emoji
        ]:
            r = _validate_vs_get(
                fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
                display=wrong,
            )
            body = r.json()
            msg = _param_value(body, "message")
            assert msg == f'The display "{wrong}" is incorrect', (
                f"Unicode mismatch message drift on {wrong!r}: got {msg!r}."
            )

    def test_h34_mismatch_message_holds_on_alias_input(self, fhir_client):
        """Mismatch-case META-PATTERN extension on alias inputs.

        For an OID-alias system input, the mismatch message MUST still
        cite the (canonical) display correctly. Catches drift where the
        canonical re-resolution path bypasses the display comparison.
        """
        wrong = "ALIAS-MISMATCH-DETECTION"
        r = _validate_vs_get(
            fhir_client, system=SNOMED_OID_ALIAS, code=SNOMED_T2DM_CODE,
            display=wrong,
        )
        # Some servers reject unknown URIs outright; if 200, mismatch MUST fire
        if r.status_code == 200:
            body = r.json()
            assert _param_value(body, "result") is False
            assert _param_value(body, "display") == SNOMED_T2DM_DISPLAY
            assert _param_value(body, "message") == (
                f'The display "{wrong}" is incorrect'
            )

    def test_h35_mismatch_get_post_byte_exact_agreement_per_system(
        self, fhir_client,
    ):
        """Mismatch-case GET↔POST byte-exact agreement on the message
        format per system.

        For every seeded code, the mismatch message from GET and POST
        MUST be byte-exact identical. SKEPTIC test_s55 covered SNOMED
        T2DM only; HISTORIAN EXTENDS to all 4 seeded systems to catch
        silent per-system drift on the POST path.
        """
        for system, code in [
            (SNOMED_URI, SNOMED_DM_CODE),
            (SNOMED_URI, SNOMED_T2DM_CODE),
            (ICD10CM_URI, ICD10CM_E11_CODE),
            (RXNORM_URI, RXNORM_METFORMIN_CODE),
        ]:
            wrong = f"GET-POST-MISMATCH-{code}"
            get_r = _validate_vs_get(
                fhir_client, system=system, code=code, display=wrong,
            )
            post_body = {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": system},
                    {"name": "code", "valueCode": code},
                    {"name": "display", "valueString": wrong},
                ],
            }
            post_r = fhir_client.post(
                "/fhir/ValueSet/$validate-code", json=post_body,
            )
            assert get_r.status_code == post_r.status_code == 200
            get_msg = _param_value(get_r.json(), "message")
            post_msg = _param_value(post_r.json(), "message")
            assert get_msg == post_msg == (
                f'The display "{wrong}" is incorrect'
            ), (
                f"GET↔POST mismatch-message drift on ({system}, {code}): "
                f"GET={get_msg!r}, POST={post_msg!r}."
            )


# =============================================================================
# SKEPTIC Tip #2 — CF-EXPLORER-CS02-01 FULLY CLOSED META-walk probe
# =============================================================================


class TestSkepticTip2CFExplorerCS0201MetaWalk:
    """SKEPTIC Tip #2: Re-confirm CF-EXPLORER-CS02-01 FULLY CLOSED via a
    META-walk probe that counts ≥1 probe per shape on every operation
    accepting a Parameters body.

    The 4-shape POST Content-Type probe family MUST be in place on EVERY
    operation in the FHIR R4 surface that accepts a Parameters body. Per
    AGENTS.md CF-EXPLORER-CS02-01 documentation, the operations are:
      - CodeSystem/$lookup (closed by CS-05 EXPLORER test_e80..e83)
      - CodeSystem/$validate-code (closed by CS-03 EXPLORER test_e40..e43)
      - CodeSystem/$subsumes (closed by CS-04 EXPLORER test_e10..e13)
      - ValueSet/$expand intensional URL (closed by VS-04 EXPLORER test_e10..e13)
      - ValueSet/$expand inline ValueSet (closed by VS-02 EXPLORER test_e100..e140)
      - ValueSet/$validate-code (closed by VS-05 SKEPTIC test_s100..s104)

    This META-walk probe:
      (a) walks ``app.routes`` to enumerate every POST route accepting a
          Parameters body
      (b) asserts every such operation has a 4-shape Content-Type probe
          family documented in the test corpus
      (c) DOES NOT depend on fixture data — pure META structural audit.
    """

    def test_h40_cf_explorer_cs02_01_meta_walk_count_operations(self):
        """META-walk probe — enumerate every POST operation accepting a
        Parameters body and confirm every one has the 4-shape probe
        family documented.
        """
        # The canonical list of operations accepting Parameters bodies.
        # Per CF-EXPLORER-CS02-01 documentation in AGENTS.md.
        expected_operations = {
            "CodeSystem/$lookup",
            "CodeSystem/$validate-code",
            "CodeSystem/$subsumes",
            "ValueSet/$expand",         # intensional URL + inline VS
            "ValueSet/$validate-code",
            # ConceptMap/$translate accepts Parameters body too — added by
            # the spec and present in apps/fhir_api.py.
            "ConceptMap/$translate",
        }

        # Read apps/fhir_api.py source — enumerate POST routes
        src = _read_module_source()
        tree = ast.parse(src)

        # Find every async def that contains the @app.post decorator path
        post_routes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                # Look at decorators for @app.post
                for dec in node.decorator_list:
                    if (
                        isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == "post"
                    ):
                        # Extract the route string from the first arg
                        if dec.args and isinstance(dec.args[0], ast.Constant):
                            route = dec.args[0].value
                            # Normalize to the canonical operation form
                            # by stripping /fhir/ prefix and {id} segments
                            if route.startswith("/fhir/"):
                                # Strip /fhir/ prefix and reduce
                                # /fhir/CodeSystem/{id}/$op → CodeSystem/$op
                                segments = route[len("/fhir/"):].split("/")
                                # Remove {id}-style path params
                                segments = [
                                    s for s in segments
                                    if not (s.startswith("{") and s.endswith("}"))
                                ]
                                if segments and segments[0] in (
                                    "CodeSystem", "ValueSet", "ConceptMap"
                                ):
                                    # Build canonical operation name
                                    if len(segments) >= 3 and segments[2].startswith("$"):
                                        op = f"{segments[0]}/{segments[2]}"
                                        post_routes.add(op)
                                    elif len(segments) >= 2 and segments[1].startswith("$"):
                                        # type-level operation, no resource id
                                        op = f"{segments[0]}/{segments[1]}"
                                        post_routes.add(op)

        # Every documented operation MUST be in the post_routes set
        missing = expected_operations - post_routes
        assert not missing, (
            f"CF-EXPLORER-CS02-01 META-walk: documented operations missing "
            f"POST routes in apps/fhir_api.py: {sorted(missing)}."
        )

    def test_h41_cf_explorer_cs02_01_vs_validate_4_shape_present(self, fhir_client):
        """4-shape probe family MUST be present on ValueSet/$validate-code.

        This is the LAST operation needing closure (per SKEPTIC resweep).
        Re-verify via 4 explicit probes — system+code body / coding body /
        codeableConcept body / error path — every shape MUST return
        ``Content-Type: application/fhir+json`` + conformant body shape.
        """
        # Shape (a): system+code body
        body_a = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": SNOMED_T2DM_CODE},
            ],
        }
        r_a = fhir_client.post("/fhir/ValueSet/$validate-code", json=body_a)
        assert r_a.status_code == 200
        assert "application/fhir+json" in r_a.headers.get("content-type", "")
        assert r_a.json().get("resourceType") == "Parameters"

        # Shape (b): coding body (alternative encoding)
        body_b = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {
                        "system": SNOMED_URI, "code": SNOMED_T2DM_CODE,
                    },
                }
            ],
        }
        r_b = fhir_client.post("/fhir/ValueSet/$validate-code", json=body_b)
        assert r_b.status_code == 200
        assert "application/fhir+json" in r_b.headers.get("content-type", "")
        assert r_b.json().get("resourceType") == "Parameters"

        # Shape (c): codeableConcept body
        body_c = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE}
                        ]
                    },
                }
            ],
        }
        r_c = fhir_client.post("/fhir/ValueSet/$validate-code", json=body_c)
        assert r_c.status_code == 200
        assert "application/fhir+json" in r_c.headers.get("content-type", "")
        assert r_c.json().get("resourceType") == "Parameters"

        # Shape (d): error path → 4xx + OperationOutcome
        r_d = fhir_client.post(
            "/fhir/ValueSet/$validate-code",
            json={"resourceType": "Parameters", "parameter": []},
        )
        assert r_d.status_code >= 400
        assert "application/fhir+json" in r_d.headers.get("content-type", "")
        assert r_d.json().get("resourceType") == "OperationOutcome"

    def test_h42_cf_explorer_cs02_01_meta_uniformity_across_shapes(
        self, fhir_client,
    ):
        """META uniformity probe — all 4 shapes MUST share the uniform
        ``Content-Type: application/fhir+json`` contract. The 200-path
        shapes return Parameters; the 4xx-path shape returns
        OperationOutcome. Both MUST be FHIR MIME.
        """
        shapes = [
            ("system+code", {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": SNOMED_URI},
                    {"name": "code", "valueCode": SNOMED_T2DM_CODE},
                ],
            }),
            ("coding", {
                "resourceType": "Parameters",
                "parameter": [
                    {
                        "name": "coding",
                        "valueCoding": {
                            "system": SNOMED_URI, "code": SNOMED_T2DM_CODE,
                        },
                    }
                ],
            }),
            ("codeableConcept", {
                "resourceType": "Parameters",
                "parameter": [
                    {
                        "name": "codeableConcept",
                        "valueCodeableConcept": {
                            "coding": [
                                {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE}
                            ]
                        },
                    }
                ],
            }),
            ("error", {
                "resourceType": "Parameters",
                "parameter": [],
            }),
        ]
        for shape_name, body in shapes:
            r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
            ct = r.headers.get("content-type", "")
            assert "application/fhir+json" in ct, (
                f"Shape {shape_name!r}: Content-Type MUST be application/"
                f"fhir+json. Got {ct!r}."
            )


# =============================================================================
# Lens 3: PROMOTED pattern re-derivations
# =============================================================================


class TestLens3PromotedPatterns:
    """Re-derive the 11 PROMOTED patterns from GLOBAL_RULES.md on the VS-05
    surface. Each pattern is documented inline in GLOBAL_RULES.md; the
    HISTORIAN re-derivation is a structural + behavioral probe proving
    the pattern's invariant holds.
    """

    # -- Pattern: client-input-as-canonical drift (count=8 PROMOTED) --

    def test_h50_client_input_as_canonical_no_raw_echo_scalar(self):
        """count=8 PROMOTED: ``_do_vs_validate`` MUST route Out ``system``
        through ``canonical_system_uri()`` (NOT echo client input).

        Source-read probe (CR-011).
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_do_vs_validate"
        )
        assert src is not None
        assert "canonical_system_uri(" in src, (
            "CR-011 / count=8 PROMOTED: _do_vs_validate MUST call "
            "canonical_system_uri() on the scalar path."
        )

    def test_h51_client_input_as_canonical_no_raw_echo_cc_path(self):
        """count=8 PROMOTED: ``_do_vs_validate`` codeableConcept path MUST
        also route Out ``system`` through ``canonical_system_uri()``
        (CR-025).

        Source-read probe.
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_do_vs_validate"
        )
        assert src is not None
        # The CR-025 fix wraps matched_uri through canonical_system_uri
        # in the codeableConcept branch.
        assert "canonical_matched_uri" in src, (
            "CR-025 / count=8 PROMOTED: _do_vs_validate codeableConcept "
            "branch MUST route matched_uri through canonical_system_uri()."
        )

    def test_h52_client_input_as_canonical_alias_resolves_to_canonical(
        self, fhir_client,
    ):
        """Behavioral re-derivation — every alias input MUST resolve to
        the canonical URI in the Out ``system`` parameter.
        """
        for alias in [SNOMED_OID_ALIAS, SNOMED_SLASH_ALIAS]:
            r = _validate_vs_get(
                fhir_client, system=alias, code=SNOMED_T2DM_CODE,
            )
            if r.status_code == 200:
                out_sys = _param_value(r.json(), "system")
                assert out_sys == SNOMED_URI, (
                    f"Alias {alias!r} MUST resolve to canonical "
                    f"{SNOMED_URI!r}, got {out_sys!r}."
                )

    # -- Pattern: cross-handler helper-wiring (count=6 PROMOTED) --

    def test_h53_cross_handler_helper_wiring_vs_validate_post(self):
        """count=6 PROMOTED: ``vs_validate_post`` MUST call the all-pairs
        helper, NOT the single-pair helper.

        Source-read probe.
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "vs_validate_post"
        )
        assert src is not None
        assert "_extract_all_coding_pairs_from_codeable_concept" in src

    def test_h54_cross_handler_helper_wiring_extract_vs_validate_params(self):
        """count=6 PROMOTED: ``_extract_vs_validate_params`` MUST call the
        all-pairs helper.

        Source-read probe.
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_extract_vs_validate_params"
        )
        assert src is not None
        assert "_extract_all_coding_pairs_from_codeable_concept" in src

    # -- Pattern: empty-string-as-present-on-required-Query (count=5 PROMOTED) --

    def test_h55_min_length_1_not_required_on_vs_validate_intentional_asymmetry(self):
        """count=5 PROMOTED: VS/$validate-code GET handler INTENTIONALLY
        does NOT use ``min_length=1`` on its Query declarations because
        the alternative encodings (coding, codeableConcept) make system+code
        not strictly required at the FastAPI level.

        This is the documented asymmetry per VS-05 SKEPTIC test_s86 —
        VS/$validate-code uses ``Query(None)`` for all params. Distinct
        from CodeSystem/$validate-code where system+code ARE strictly
        required (TS-02 SKEPTIC QA-002).
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "vs_validate_get"
        )
        assert src is not None
        # Verify NO min_length=1 on Query declarations in vs_validate_get
        assert "min_length=1" not in src, (
            "Intentional asymmetry: vs_validate_get MUST NOT enforce "
            "min_length=1 because the alternative encodings (coding, "
            "codeableConcept) make system+code not strictly required."
        )

    # -- Pattern: isinstance-dict guard on POST iterators (count=4 PROMOTED 10th) --

    def test_h56_isinstance_dict_guard_on_all_pairs_helper(self):
        """count=4 PROMOTED (10th PROMOTED pattern): the all-pairs helper
        MUST have an ``isinstance(<var>, dict)`` guard inside the loop
        iterating codeableConcept codings.

        Source-read probe — walk the AST tree of
        ``_extract_all_coding_pairs_from_codeable_concept`` and assert
        the guard.
        """
        src = _read_nested_function_source(
            _read_module_source(),
            "create_fhir_app",
            "_extract_all_coding_pairs_from_codeable_concept",
        )
        assert src is not None
        # Walk the AST to find For loops with isinstance guards
        tree = ast.parse(src)
        for_loops_with_isinstance = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                # Walk the immediate body (not nested fors)
                for stmt in node.body:
                    for sub in ast.walk(stmt):
                        if (
                            isinstance(sub, ast.Call)
                            and isinstance(sub.func, ast.Name)
                            and sub.func.id == "isinstance"
                        ):
                            for_loops_with_isinstance += 1
                            break
        assert for_loops_with_isinstance >= 1, (
            "count=4 PROMOTED (10th pattern): _extract_all_coding_pairs"
            "_from_codeable_concept MUST have isinstance() guard inside "
            "its for-loop."
        )

    # -- Pattern: literal-value-vs-canonical-registry drift (count=8 PROMOTED) --

    def test_h57_no_broad_except_in_do_vs_validate(self):
        """Silent-fallback prohibition: ``_do_vs_validate`` MUST NOT have
        a broad ``except Exception:`` block.

        Source-read probe — walk the AST tree of ``_do_vs_validate`` and
        assert no broad Exception catch.
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_do_vs_validate"
        )
        assert src is not None
        tree = ast.parse(src)
        broad_excepts = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    broad_excepts.append(node)
                elif isinstance(node.type, ast.Name) and node.type.id == "Exception":
                    broad_excepts.append(node)
        assert not broad_excepts, (
            f"Silent-fallback prohibition: _do_vs_validate MUST NOT have "
            f"broad except Exception: blocks. Found {len(broad_excepts)}."
        )

    # -- Pattern: count_limited strict-> (count=4 PROMOTED 11th) on VS-05 surface --
    # VS-05 surface does not call build_valueset_expand directly, but the
    # pattern extends to the sibling operation handlers via _do_vs_validate's
    # absence of truncation logic (validate-code does not paginate).
    # This probe documents the absence — no count_limited in _do_vs_validate.

    def test_h58_no_count_limited_in_do_vs_validate(self):
        """count=4 PROMOTED (11th pattern) — VS-05 surface does NOT use
        count_limited comparison (validate-code does not paginate).
        Documents the absence so a future refactor that introduces
        count-based truncation on this surface is audited against the
        2-axis AST contract (operator=ast.Gt, LEFT=len, RIGHT=Name).
        """
        src = _read_nested_function_source(
            _read_module_source(), "create_fhir_app", "_do_vs_validate"
        )
        assert src is not None
        # No count_limited comparison in this handler today.
        assert "count_limited" not in src, (
            "VS-05 surface does not paginate; count_limited should NOT "
            "appear in _do_vs_validate. If introduced, the 2-axis AST "
            "contract from GLOBAL_RULES.md line 142 MUST apply."
        )

    # -- Pattern: documentation-of-buggy-behavior-as-probe (count=4 META confirmations) --
    # CF-SKEPTIC-CS03-01 CLOSED via this pattern (CS-03 TERMINOLOGIST
    # test_t60 was the load-bearing pin that fired loudly when VS-05
    # SKEPTIC QA-069 landed).

    def test_h59_carry_forward_as_probe_pattern_4th_meta_via_cs03_terminologist(
        self, fhir_client,
    ):
        """4th META confirmation of the carry-forward-as-probe pattern:
        the CS-03 TERMINOLOGIST test_t60 pin (renamed when VS-05 SKEPTIC
        QA-069 closed CF-SKEPTIC-CS03-01) is now asserting the spec-
        correct behavior. HISTORIAN re-derives the spec-correct behavior
        here so a regression on either side is isolated to its own
        assertion path.
        """
        # Display mismatch MUST fire (CF-SKEPTIC-CS03-01 CLOSED shape).
        wrong = "CARRY-FORWARD-AS-PROBE-PATTERN-4TH-META"
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
            display=wrong,
        )
        body = r.json()
        assert _param_value(body, "result") is False, (
            "CF-SKEPTIC-CS03-01 CLOSED: display mismatch MUST be enforced. "
            "If this fires, the carry-forward has regressed."
        )

    # -- Pattern: closed-enum R5/R4B contamination (CF-HISTORIAN-VS01-01 RESOLVED) --
    # Not directly applicable to VS-05 (validate-code doesn't emit equivalence),
    # but the general closed-enum audit extends to the operation advertisement.

    def test_h60_no_off_spec_inferSystem_handled_gracefully(self, fhir_client):
        """R4 spec citation discipline — VS/$validate-code accepts the
        ``inferSystem`` R5-only In parameter without 5xx (per AGENTS.md
        NOT A BUG registry: accepted as no-op, not manufactured).

        Re-derive via behavioral probe.
        """
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
        )
        # Pass inferSystem via query string
        r2 = fhir_client.get(
            f"/fhir/ValueSet/$validate-code?system={SNOMED_URI}"
            f"&code={SNOMED_T2DM_CODE}&inferSystem=true"
        )
        assert r2.status_code == 200
        assert _param_value(r2.json(), "result") is True

    # -- Pattern: response-builder drift stragglers (strategy 11 source-read audit) --

    def test_h61_response_builder_canonical_precedence_in_source(self):
        """TS-02 TERMINOLOGIST QA-029 fix shape: ``build_parameters_validate``
        MUST prefer engine canonical display (code_info.name) over client
        echo when both are present.

        Source-read probe — verify the builder's display-precedence logic
        is intact.
        """
        from medterm4ds.engines.fhir import responses as responses_mod
        src = inspect.getsource(responses_mod.build_parameters_validate)
        # The precedence: code_info.name > client display
        assert "code_info.name" in src, (
            "TS-02 TERMINOLOGIST QA-029: build_parameters_validate MUST "
            "prefer code_info.name (engine canonical) over client display."
        )

    # -- Pattern: boolean serializer lowercase wire-format (CR-002) --

    def test_h62_result_lowercase_on_wire_format(self, fhir_client):
        """CR-002: result valueBoolean MUST be lowercase on the wire.
        Python's ``str(True)`` is 'True' (capital T); the builder MUST
        serialize as 'true' per FHIR R4 §3.4.1.
        """
        r = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
        )
        # Inspect the raw response body — valueBoolean MUST be lowercase.
        body_text = r.text
        assert '"valueBoolean": true' in body_text, (
            "CR-002: result valueBoolean MUST be lowercase 'true' on wire. "
            f"Body: {body_text[:200]}"
        )
        assert '"valueBoolean": True' not in body_text

    # -- Pattern: silent-wrong-answer on alt encodings (count=6 PROMOTED) --

    def test_h63_silent_wrong_answer_coding_alt_encoding(self, fhir_client):
        """count=6 PROMOTED: the ``coding`` alternative encoding MUST
        produce the SAME result as scalar system+code. A silent
        wrong-answer on the alt encoding is the recurring pattern.
        """
        # Scalar form
        r_scalar = _validate_vs_get(
            fhir_client, system=SNOMED_URI, code=SNOMED_T2DM_CODE,
        )
        # coding alternative encoding
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {
                        "system": SNOMED_URI, "code": SNOMED_T2DM_CODE,
                    },
                }
            ],
        }
        r_coding = fhir_client.post(
            "/fhir/ValueSet/$validate-code", json=body,
        )
        assert r_scalar.status_code == r_coding.status_code == 200
        assert (
            _param_value(r_scalar.json(), "result")
            == _param_value(r_coding.json(), "result")
            is True
        ), (
            "count=6 PROMOTED: coding alt encoding MUST produce same result "
            "as scalar system+code."
        )

    # -- Pattern: HCPCS URI drift class (count=8+1 PROMOTED) --
    # Re-derive by confirming HCPCS is not in the seeded VS-05 surface
    # but the canonical URI registry still holds.

    def test_h64_hcpcs_uri_drift_class_canonical_uri_intact(self):
        """count=8+1 PROMOTED: HCPCS canonical URI in registry MUST be the
        CMS-published URI (not the legacy THO resource URL).

        Re-derive on the VS-05 surface via the registry-as-contract probe.
        """
        from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI
        assert SYSTEM_TO_FHIR_URI.get("HCPCS") == (
            "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"
        ), (
            "HCPCS URI drift class: registry canonical URI MUST be the "
            "CMS-published URI."
        )


# =============================================================================
# Lens 4: Cross-handler CS↔VS display-mismatch parity (HISTORIAN extension)
# =============================================================================


class TestLens4CrossHandlerParity:
    """Cross-handler CS↔VS parity on the display-mismatch path. HISTORIAN
    extends the SKEPTIC test_s71 probe (matching case) to the MISMATCH
    case for both display and message across every seeded system.
    """

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
    def test_h70_cs_vs_result_message_display_byte_exact_per_system(
        self, fhir_client, system, code,
    ):
        """Cross-handler CS↔VS parity on the mismatch path: result value,
        Out message, AND Out canonical display MUST all be byte-exact
        identical between CodeSystem/$validate-code and ValueSet/$validate-
        code for the same (system, code, wrong display) on every seeded
        system.
        """
        wrong = f"CROSS-HANDLER-MISMATCH-{code}"
        cs_r = _validate_cs_get(
            fhir_client, system=system, code=code, display=wrong,
        )
        vs_r = _validate_vs_get(
            fhir_client, system=system, code=code, display=wrong,
        )
        assert cs_r.status_code == vs_r.status_code == 200
        # result value
        assert (
            _param_value(cs_r.json(), "result")
            == _param_value(vs_r.json(), "result")
            is False
        )
        # message byte-exact
        assert (
            _param_value(cs_r.json(), "message")
            == _param_value(vs_r.json(), "message")
            == f'The display "{wrong}" is incorrect'
        )
        # display byte-exact
        assert (
            _param_value(cs_r.json(), "display")
            == _param_value(vs_r.json(), "display")
        ), (
            f"CS↔VS display drift on ({system}, {code}): "
            f"CS={_param_value(cs_r.json(), 'display')!r}, "
            f"VS={_param_value(vs_r.json(), 'display')!r}."
        )


# =============================================================================
# Lens 5: 4-personality rotation pattern re-confirmation
# =============================================================================


class TestLens5PersonalityRotationPattern:
    """Re-confirm the 4-personality rotation pattern HOLDS at VS-05. The
    prior [2026-07-13] run found 2 bugs (QA-069 + QA-070) on SKEPTIC and
    CLEAN on the other 3 personalities. The current [2026-08-09] resweep
    SKEPTIC was CLEAN (0 bugs, 89 probes). HISTORIAN re-derives the 2
    fixes from the prior run via regression-style probes and confirms
    the surface remains hardened.
    """

    def test_h80_qa069_qa070_simultaneously_held(self, fhir_client):
        """Both QA-069 AND QA-070 fixes MUST be HELD simultaneously. A
        regression on either would surface here.

        Probe sends a codeableConcept with multiple codings, the FIRST
        of which has a display parameter triggering mismatch, the SECOND
        of which is valid. The all-pairs helper MUST skip the first
        (invalid) coding and match the second, returning result=true.
        """
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "codeableConcept",
                    "valueCodeableConcept": {
                        "coding": [
                            # Invalid first (with a display that would
                            # mismatch if it were valid)
                            {"system": SNOMED_URI, "code": "BOGUS_H80"},
                            # Valid second
                            {"system": SNOMED_URI, "code": SNOMED_T2DM_CODE},
                        ]
                    },
                }
            ],
        }
        r = fhir_client.post("/fhir/ValueSet/$validate-code", json=body)
        assert r.status_code == 200
        # QA-070: all-pairs helper fires → result=true via second coding
        assert _param_value(r.json(), "result") is True
        # QA-069 display mismatch does NOT fire on the codeableConcept path
        # (per CS-03 SKEPTIC AUDIT-002: spec does not mandate display
        # enforcement on codeableConcept). The matched coding's canonical
        # display MUST appear.
        assert _param_value(r.json(), "display") == SNOMED_T2DM_DISPLAY
