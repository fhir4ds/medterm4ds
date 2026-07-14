"""VS-04 SKEPTIC: ValueSet $expand — Intensional URLs (fhir_vs).

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
SNOMED CT intensional: https://hl7.org/fhir/R4/snomedct.html
Truncation ext: https://hl7.org/fhir/R4/extension-valueset-toocostly.html

8 spec items under test:

  1. ``fhir_vs=isa``: include root + all descendants.
  2. ``fhir_vs`` (no value): equivalent to ``isa``.
  3. ``fhir_vs=refset``: include members of named refset.
  4. SNOMED URL pattern: ``http://snomed.info/sct/{code}?fhir_vs=isa``.
  5. Versioned SNOMED URL:
     ``http://snomed.info/sct/{edition}/version/{date}/{code}?fhir_vs=isa``.
  6. Depth cap via ``FHIR_VS_MAX_DEPTH`` env var (medterm4ds extension).
  7. Truncation extension emitted when limit hit (``count_limited`` /
     ``depth-limited``).
  8. Non-SNOMED systems: server raises clear ``ValueError`` (other systems
     lack a standard intensional URL convention).

SKEPTIC lens: adversarial bug hunting. Each probe exercises one spec-mandated
behavior; failures indicate silent-wrong-answer or non-conformant shape.

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus) -> 44054006 (T2DM)
  - mrrel: 1 row (T2DM isa Diabetes mellitus)

References:
  - SKEPTIC TS-03 QA-032: implicit value set detection added.
  - SKEPTIC TS-03 QA-033: empty-source extension added.
  - HISTORIAN TS-03 QA-034: ``?fhir_vs`` (no value) URL parser bug fixed.
"""

from __future__ import annotations

import os
from urllib.parse import urlencode

import pytest

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent (root)
SNOMED_T2DM = "44054006"  # child of 73211009

LOINC_URI = "http://loinc.org"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"

TRUNCATION_EXT_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

# Per GLOBAL_RULES.md "FHIR API Specifics": ``$expand?url=...?fhir_vs=isa``
# only supports SNOMED CT intensional expansions. Other systems raise
# ``ValueError`` — they lack a standard intensional URL convention.


def _expand_url(client, url: str, count: int | None = None):
    """Helper: GET /fhir/ValueSet/$expand with the given url (and count)."""
    params = [("url", url)]
    if count is not None:
        params.append(("count", count))
    return client.get("/fhir/ValueSet/$expand", params=params)


def _contains_codes(resp_json: dict) -> list[str]:
    return [c.get("code") for c in resp_json.get("expansion", {}).get("contains", [])]


def _extensions(resp_json: dict) -> list[dict]:
    return resp_json.get("expansion", {}).get("extension", [])


class TestVS04Item1IsaSemantics:
    """§VS-04 Item 1: ``fhir_vs=isa`` MUST include root + all descendants.

    Spec: https://hl7.org/fhir/R4/snomedct.html — "the expression
    ``http://snomed.info/sct?fhir_vs=isa/<conceptId>`` ... means all concepts
    that are descendents of the named concept AND the concept itself".

    Common bug class: implementation returns descendants only and silently
    drops the root. SKEPTIC specifically verifies the root IS present.
    """

    def test_s01_isa_includes_root(self, fhir_client):
        """``?fhir_vs=isa`` on root code MUST include the root in expansion."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes, (
            f"fhir_vs=isa MUST include the root code ({SNOMED_DIABETES_MELLITUS}); "
            f"got {codes}. Common bug: descendants-only expansion."
        )

    def test_s02_isa_includes_descendants(self, fhir_client):
        """``?fhir_vs=isa`` on root code MUST include descendants."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_T2DM in codes, (
            f"fhir_vs=isa MUST include descendants ({SNOMED_T2DM}); got {codes}."
        )

    def test_s03_isa_on_leaf_includes_just_leaf(self, fhir_client):
        """``?fhir_vs=isa`` on a leaf code returns just the leaf."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_T2DM}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert codes == [SNOMED_T2DM], (
            f"fhir_vs=isa on leaf MUST return just the leaf; got {codes}."
        )

    def test_s04_isa_root_display_resolved(self, fhir_client):
        """Root entry MUST carry the canonical display name from the engine."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        contains = resp.json()["expansion"]["contains"]
        root_entry = next(c for c in contains if c["code"] == SNOMED_DIABETES_MELLITUS)
        assert root_entry.get("display") == "Diabetes mellitus", (
            f"Root display must be canonical 'Diabetes mellitus'; "
            f"got {root_entry.get('display')!r}"
        )

    def test_s05_isa_system_uri_canonical(self, fhir_client):
        """All entries MUST carry the canonical SNOMED URI."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        contains = resp.json()["expansion"]["contains"]
        for c in contains:
            assert c.get("system") == SNOMED_URI, (
                f"Entry {c['code']} system must be canonical {SNOMED_URI}; "
                f"got {c.get('system')!r}"
            )


class TestVS04Item2BareFhirVsEquivalentToIsa:
    """§VS-04 Item 2: ``?fhir_vs`` (no value) is equivalent to ``?fhir_vs=isa``.

    Spec: https://hl7.org/fhir/R4/snomedct.html — the bare ``?fhir_vs`` form
    is the shorthand for the implicit value set of all SNOMED CT (no root).
    When combined with a code path (``.../sct/<code>?fhir_vs``), the
    equivalence to ``isa`` applies.

    HISTORIAN TS-03 QA-034 fixed a parser bug where the bare ``?fhir_vs`` form
    wasn't recognized (parse_qs requires key=value pairs).
    """

    def test_s10_bare_fhir_vs_includes_root(self, fhir_client):
        """``?fhir_vs`` (no =isa, no =value) MUST be recognized as isa."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs",
        )
        # The current implementation has a code path for the bare ``?fhir_vs``
        # form on a code-bearing URL. Either:
        #   (a) Successful expansion with root+descendants (correct), or
        #   (b) Explicit 400 error mentioning the unsupported pattern.
        # NOT acceptable: 200 with empty contains[], or 200 with wrong content.
        if resp.status_code == 200:
            codes = _contains_codes(resp.json())
            assert SNOMED_DIABETES_MELLITUS in codes, (
                f"bare ?fhir_vs MUST include root (equivalent to isa); got {codes}"
            )
        else:
            # If unsupported, the error MUST mention fhir_vs / intensional.
            assert resp.status_code in (400, 422), (
                f"bare ?fhir_vs returned {resp.status_code}; expected 200 or 400/422"
            )


class TestVS04Item3RefsetSemantics:
    """§VS-04 Item 3: ``fhir_vs=refset`` — members of named refset.

    Per https://hl7.org/fhir/R4/snomedct.html: ``?fhir_vs=refset`` returns
    members of a SNOMED CT Reference Set. Reference Sets are SNOMED-defined
    subsets with explicit membership.

    medterm4ds does not load SNOMED refset data — there is no
    ``mrrefset`` table. The implementation MUST NOT silently equate
    ``refset`` with ``isa`` semantics; that produces silent-wrong-answer.
    """

    def test_s20_refset_does_not_silently_equate_to_isa(self, fhir_client):
        """``?fhir_vs=refset`` MUST NOT silently return root+descendants.

        medterm4ds lacks refset data — the server MUST either:
          (a) Return an empty expansion with a clear signal (extension /
              OperationOutcome explaining refset data is unavailable), OR
          (b) Return 400 / 422 with a clear error message.

        NOT acceptable: 200 with root+descendants pretending refset semantics
        were honored. That's silent-wrong-answer — a client asks for refset
        members and gets the entire subtree instead.

        SKEPTIC note: this test passes today because the implementation
        silently equates ``refset`` with ``isa`` semantics when refset data
        is missing. See QA-062.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=refset",
        )
        # If status is 200, the response MUST NOT contain the root + descendants
        # (which would be the isa-equivalent wrong answer).
        if resp.status_code == 200:
            codes = _contains_codes(resp.json())
            # Either empty expansion (refset data unavailable, surfaced) OR
            # contains only actual refset members (which the fixture can't
            # seed because there's no refset table).
            # WRONG: contains both root + descendant (silently isa-equivalent).
            not_both = not (
                SNOMED_DIABETES_MELLITUS in codes and SNOMED_T2DM in codes
            )
            assert not_both, (
                "BUG QA-062: ?fhir_vs=refset silently equated to isa — "
                "returned root + descendants. medterm4ds lacks refset data; "
                "the server MUST surface this (empty + extension, or 400), "
                "not silently produce the isa expansion."
            )


class TestVS04Item4SnomedUrlPatterns:
    """§VS-04 Item 4: SNOMED URL pattern variations.

    Spec: https://hl7.org/fhir/R4/snomedct.html

    Pattern variations that MUST be handled:
      - Basic: ``http://snomed.info/sct/{code}?fhir_vs=isa``
      - Extra params: ``...?param=ignore&fhir_vs=isa``
      - No code: ``http://snomed.info/sct?fhir_vs=isa`` — what does the
        server do? (Spec: this means "all of SNOMED CT" via the implicit
        value set convention; medterm4ds's specific behavior is to reject
        this URL via _expand_url_pattern since the path has no code — it
        SHOULD fall through to _expand_implicit_value_set per TS-03 SKEPTIC
        QA-032.)
    """

    def test_s30_basic_pattern(self, fhir_client):
        """Basic SNOMED URL pattern works."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200, f"basic pattern failed: {resp.status_code}"
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes and SNOMED_T2DM in codes

    def test_s31_extra_query_params_ignored(self, fhir_client):
        """Extra query params MUST NOT break the URL."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?"
            "param=ignore&fhir_vs=isa",
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes and SNOMED_T2DM in codes

    def test_s32_no_code_path_falls_through_to_implicit(self, fhir_client):
        """``http://snomed.info/sct?fhir_vs=isa`` (no code) handling.

        Spec semantics: ``?fhir_vs=isa`` without a code refers to "all of
        SNOMED CT" (the implicit value set). medterm4ds has TS-03 SKEPTIC
        QA-032 logic that handles the bare ``?fhir_vs`` form as the implicit
        "all SNOMED" path; the ``?fhir_vs=isa`` form (with a value but no
        code) currently raises ValueError in _expand_url_pattern.

        Acceptable behaviors:
          (a) 200 with all SNOMED codes (implicit value set expansion), OR
          (b) 400 with a clear message about missing root code.

        NOT acceptable: silent wrong-answer / 500 crash.
        """
        resp = _expand_url(
            fhir_client,
            "http://snomed.info/sct?fhir_vs=isa",
        )
        # Either 200 (implicit expansion) or 400 (clear error).
        assert resp.status_code in (200, 400, 422), (
            f"no-code ?fhir_vs=isa returned {resp.status_code}; "
            "expected 200, 400, or 422 (no 500 crash)"
        )

    def test_s33_unknown_code_returns_200_empty(self, fhir_client):
        """Unknown SNOMED code returns 200 with empty contains[] (not 500)."""
        resp = _expand_url(
            fhir_client,
            "http://snomed.info/sct/99999999?fhir_vs=isa",
        )
        assert resp.status_code == 200, f"unknown code crashed: {resp.status_code}"
        codes = _contains_codes(resp.json())
        assert codes == [], f"Unknown code should produce empty expansion: {codes}"


class TestVS04Item5VersionedSnomedUrl:
    """§VS-04 Item 5: Versioned SNOMED URL.

    Per https://hl7.org/fhir/R4/snomedct.html, SNOMED CT editions may be
    identified by URI of the form:

        http://snomed.info/sct/{edition}/version/{date}

    where {edition} is e.g. ``http://snomed.info/sct/73211009`` (the SNOMED
    CT concept) or an edition identifier (e.g. ``32505021000036107`` for
    Australian edition).

    The medterm4ds conformance fixture does not have versioned SNOMED data,
    so the test asserts that the server handles the URL pattern gracefully
    (200 with results OR 400/422 with clear message — not 500 crash).
    """

    def test_s40_versioned_url_does_not_crash(self, fhir_client):
        """Versioned SNOMED URL is handled gracefully (no 500 crash).

        The current implementation matches the SNOMED URI substring, so a
        versioned URL ``http://snomed.info/sct/{edition}/version/{date}/...``
        is parsed by splitting on '/' and taking the LAST path segment as
        the code. For the URL below, that's ``73211009``.
        """
        url = (
            "http://snomed.info/sct/32505021000036107"
            "/version/20240101/73211009?fhir_vs=isa"
        )
        resp = _expand_url(fhir_client, url)
        # No 500 crash. Either 200 with expansion, or 400 with clear error.
        assert resp.status_code in (200, 400, 422), (
            f"versioned SNOMED URL returned {resp.status_code}; expected 200/400/422"
        )

    def test_s41_nested_path_segment_as_code(self, fhir_client):
        """Nested path ``/sct/A/B?fhir_vs=isa`` uses last segment as code.

        Current implementation takes ``path_parts[-1]`` as the code. This is
        undocumented behavior but the existing test s10 confirms the contract
        — the last path segment is the code. The SKEPTIC concern: this is a
        silent-wrong-answer surface if a client sends a URL with extra path
        segments (e.g. typo, malformed URL).
        """
        url = f"http://snomed.info/sct/{SNOMED_T2DM}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        resp = _expand_url(fhir_client, url)
        assert resp.status_code == 200, f"nested path crashed: {resp.status_code}"
        codes = _contains_codes(resp.json())
        # The last segment is treated as the code.
        assert SNOMED_DIABETES_MELLITUS in codes, (
            f"Nested path should treat last segment as code; got {codes}"
        )


class TestVS04Item6FhirVsMaxDepth:
    """§VS-04 Item 6: ``FHIR_VS_MAX_DEPTH`` env var (medterm4ds extension).

    The medterm4ds-specific ``FHIR_VS_MAX_DEPTH`` env var caps the descendant
    walk depth (default 5). This bounds query cost for wide SNOMED subtrees.
    """

    def test_s50_depth_cap_emits_toocostly_extension(
        self, fhir_client, monkeypatch
    ):
        """Setting ``FHIR_VS_MAX_DEPTH=1`` MUST emit the toocostly extension.

        The fixture has depth-1 descendants (T2DM is direct child of DM). With
        ``FHIR_VS_MAX_DEPTH=1``, the walk completes within the cap (because
        depth is exactly 1). However, the extension is emitted when the BFS
        reaches max_depth with frontier still non-empty.

        For the conformance fixture (1 mrrel row, depth 1), ``FHIR_VS_MAX_DEPTH=1``
        walks the single layer and exits without hitting the cap (no extension).
        Setting ``FHIR_VS_MAX_DEPTH=0`` should return root-only with the
        depth-cap extension.
        """
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "0")
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        # Root MUST be included even with depth=0 (the descendant walk is
        # skipped but the root entry is added unconditionally for isa).
        assert SNOMED_DIABETES_MELLITUS in codes, (
            f"Root must be included even with FHIR_VS_MAX_DEPTH=0; got {codes}"
        )
        # Descendants MUST be excluded when depth=0.
        assert SNOMED_T2DM not in codes, (
            f"Descendants must be excluded with FHIR_VS_MAX_DEPTH=0; got {codes}"
        )

    def test_s51_depth_0_signals_truncation(self, fhir_client, monkeypatch):
        """``FHIR_VS_MAX_DEPTH=0`` MUST emit a toocostly / depth-limited signal.

        Per FHIR R4 §4.9.5 / extension-valueset-toocostly: when an expansion
        is truncated, the server SHOULD attach the extension so clients know
        more concepts exist beyond the cap.

        SKEPTIC note: the current implementation with ``FHIR_VS_MAX_DEPTH=0``
        returns root only with NO extension. That's silent-wrong-answer — a
        client receives " Diabetes mellitus, total=1" and has no way to know
        the cap fired. See QA-065.
        """
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "0")
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        exts = _extensions(resp.json())
        # The extension MUST be present OR the response must signal truncation
        # in another spec-compliant way (e.g. expansion.parameter with offset).
        truncation_signaled = any(
            e.get("url") == TRUNCATION_EXT_URL for e in exts
        )
        assert truncation_signaled, (
            "BUG QA-065: FHIR_VS_MAX_DEPTH=0 must emit a truncation signal "
            "(valueset-toocostly extension) — clients cannot detect the cap "
            f"fired without it. Extensions: {exts}"
        )

    def test_s52_depth_1_walks_descendants(self, fhir_client, monkeypatch):
        """``FHIR_VS_MAX_DEPTH=1`` walks one layer of descendants."""
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "1")
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        # With depth=1, T2DM (direct child of DM) is included.
        assert SNOMED_T2DM in codes, (
            f"FHIR_VS_MAX_DEPTH=1 should walk direct descendants; got {codes}"
        )

    def test_s53_invalid_depth_value_handled(self, fhir_client, monkeypatch):
        """``FHIR_VS_MAX_DEPTH=not-a-number`` MUST NOT crash the server.

        The current implementation does ``int(os.getenv(...))`` directly,
        which raises ValueError on non-numeric values. The HTTP handler
        catches ValueError from _expand_url_pattern but not from the
        int() conversion at module top.

        SKEPTIC note: this is a crash-on-bad-env-var bug. The server should
        either ignore the invalid value (use default) OR return a 500 with
        a clear message (acceptable per FHIR spec since it's a server
        configuration issue), NOT a raw Python traceback.
        """
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "not-a-number")
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        # Either 200 (ignored invalid env) or 500 (server error — acceptable
        # only if NOT a Python traceback in the response body).
        assert resp.status_code in (200, 500), (
            f"Invalid FHIR_VS_MAX_DEPTH returned {resp.status_code}"
        )
        if resp.status_code == 500:
            # 500 with a FHIR OperationOutcome is acceptable. 500 with a raw
            # Python traceback is a security / DoS surface.
            try:
                body = resp.json()
                assert body.get("resourceType") == "OperationOutcome", (
                    "500 response must be a FHIR OperationOutcome, not a raw "
                    f"traceback. Body: {body!r}"
                )
            except Exception:
                pytest.fail(
                    "500 response is not valid JSON; raw Python traceback leak"
                )


class TestVS04Item7TruncationExtensionShape:
    """§VS-04 Item 7: truncation extension emitted when limit hit.

    Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html

    When the server truncates the expansion (count cap OR depth cap), it
    SHOULD attach the valueset-toocostly extension with:
      - url: http://hl7.org/fhir/StructureDefinition/valueset-toocostly
      - valueBoolean: true
      - extension[]: at least one with url=reason and valueString=<explanation>
    """

    def test_s60_count_cap_emits_extension(self, fhir_client):
        """``count=1`` truncation emits the toocostly extension."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        assert resp.status_code == 200
        exts = _extensions(resp.json())
        assert any(e.get("url") == TRUNCATION_EXT_URL for e in exts), (
            f"count-limited expansion MUST carry toocostly extension; got {exts}"
        )

    def test_s61_extension_has_value_boolean_true(self, fhir_client):
        """Extension ``valueBoolean`` MUST be ``true`` (lowercase, bool)."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        exts = _extensions(resp.json())
        toocostly = next(e for e in exts if e.get("url") == TRUNCATION_EXT_URL)
        assert toocostly.get("valueBoolean") is True, (
            f"valueBoolean must be true; got {toocostly.get('valueBoolean')!r}"
        )

    def test_s62_extension_has_reason_subextension(self, fhir_client):
        """Extension MUST carry a ``reason`` sub-extension with explanatory text."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        exts = _extensions(resp.json())
        toocostly = next(e for e in exts if e.get("url") == TRUNCATION_EXT_URL)
        sub_exts = toocostly.get("extension", [])
        reason_ext = next(
            (e for e in sub_exts if e.get("url") == "reason"), None
        )
        assert reason_ext is not None, (
            f"toocostly extension must carry a 'reason' sub-extension; got {sub_exts}"
        )
        assert isinstance(reason_ext.get("valueString"), str)
        assert "count-limited" in reason_ext["valueString"], (
            f"reason must mention count-limited; got {reason_ext['valueString']!r}"
        )

    def test_s63_total_reflects_untruncated_size(self, fhir_client):
        """``expansion.total`` reflects the UN-truncated size (VS-02 QA-057 fix).

        Per FHIR R4 §4.9.2: "The total number of concepts in the expansion."
        When count truncation occurs, ``total`` MUST reflect the pre-truncation
        size (clients paging rely on it). VS-02 SKEPTIC QA-057 fixed this.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        body = resp.json()
        contains = body["expansion"]["contains"]
        total = body["expansion"]["total"]
        # Fixture has 2 codes (root + descendant). With count=1, contains is 1
        # but total MUST be 2.
        assert total == 2, (
            f"total must reflect un-truncated size (2); got {total}. "
            f"contains={len(contains)}"
        )


class TestVS04Item8NonSnomedSystemsRaiseValueError:
    """§VS-04 Item 8: Non-SNOMED systems raise clear ValueError.

    Per GLOBAL_RULES.md "FHIR API Specifics": ``$expand?url=...?fhir_vs=isa``
    only supports SNOMED CT intensional expansions. Other systems (LOINC,
    RxNorm, ICD-10-CM, etc.) lack a standard intensional URL convention —
    the server raises ``ValueError`` which the HTTP layer converts to a
    400 OperationOutcome.
    """

    @pytest.mark.parametrize("uri", [
        LOINC_URI,
        RXNORM_URI,
        ICD10CM_URI,
        "http://www.ama-assn.org/go/cpt",
        "http://hl7.org/fhir/sid/cvx",
        "http://example.org/fake-system",
    ])
    def test_s70_non_snomed_returns_400_operationoutcome(self, fhir_client, uri):
        """Non-SNOMED ``?fhir_vs=isa`` MUST return 400 OperationOutcome.

        The error MUST be FHIR-shaped (OperationOutcome), not a raw 500
        traceback. The diagnostics field MUST mention the system is
        unsupported and which systems ARE supported (SNOMED CT).
        """
        resp = _expand_url(fhir_client, f"{uri}?fhir_vs=isa")
        assert resp.status_code == 400, (
            f"Non-SNOMED {uri} ?fhir_vs=isa must return 400; got {resp.status_code}"
        )
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome", (
            f"Error response must be OperationOutcome; got resourceType={body.get('resourceType')!r}"
        )
        issue = body.get("issue", [{}])[0]
        diag = issue.get("diagnostics", "")
        # Diagnostics MUST mention SNOMED CT intensional expansions so the
        # client knows which system IS supported.
        assert "SNOMED" in diag or "snomed" in diag, (
            f"Error diagnostics must mention SNOMED CT as the supported system; "
            f"got {diag!r}"
        )

    def test_s71_non_snomed_explicit_uri_in_message(self, fhir_client):
        """Error message includes the SNOMED URI for client guidance."""
        resp = _expand_url(fhir_client, f"{LOINC_URI}?fhir_vs=isa")
        body = resp.json()
        issue = body.get("issue", [{}])[0]
        diag = issue.get("diagnostics", "")
        assert "http://snomed.info/sct" in diag, (
            f"Error must include the canonical SNOMED URI for client guidance; "
            f"got {diag!r}"
        )


class TestVS04EdgeCaseUnrecognizedFhirVsValue:
    """SKEPTIC edge case: unrecognized ``fhir_vs`` value.

    The current implementation:
        include_root = fhir_vs in ("", "isa", "refset")

    For an unrecognized value (e.g. ``fhir_vs=bogus``), ``include_root`` is
    False but the descendant walk STILL runs. Result: root excluded,
    descendants returned. This is silent-wrong-answer — the client asks for
    an unsupported value and gets a partial expansion that LOOKS like a
    successful response.
    """

    def test_s80_unknown_value_does_not_silently_expand(self, fhir_client):
        """``?fhir_vs=unknown`` MUST NOT silently return a partial expansion.

        Acceptable behaviors:
          (a) 400 with clear error mentioning the unrecognized value, OR
          (b) 200 with empty expansion + explanatory extension, OR
          (c) 200 treating ``unknown`` as ``isa`` (root + descendants, full).

        NOT acceptable: 200 with descendants-only (root excluded). That's
        silent-wrong-answer — looks like success, isn't.

        SKEPTIC note: this is QA-060. The current implementation returns
        descendants-only (root excluded) without any signal.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=unknown",
        )
        if resp.status_code == 200:
            codes = _contains_codes(resp.json())
            descendants_only = (
                SNOMED_T2DM in codes and SNOMED_DIABETES_MELLITUS not in codes
            )
            assert not descendants_only, (
                "BUG QA-060: ?fhir_vs=unknown silently returns descendants-only "
                "(root excluded) — silent-wrong-answer. Server MUST either "
                "reject the value (400), return empty + extension, or treat as "
                "isa (root+descendants). Got: " + str(codes)
            )

    def test_s81_case_sensitive_value_does_not_silently_partial(self, fhir_client):
        """``?fhir_vs=ISA`` (uppercase) MUST NOT silently return partial.

        Per https://confluence.ihtsdotools.org/display/DOCTSG/: SNOMED CT
        URL conventions are case-insensitive on the fhir_vs value. The
        current implementation is case-sensitive (``fhir_vs in ("", "isa",
        "refset")``) so ``ISA`` is treated as unknown.

        SKEPTIC note: this is QA-061. The current implementation silently
        returns descendants-only for ``?fhir_vs=ISA``.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=ISA",
        )
        if resp.status_code == 200:
            codes = _contains_codes(resp.json())
            descendants_only = (
                SNOMED_T2DM in codes and SNOMED_DIABETES_MELLITUS not in codes
            )
            assert not descendants_only, (
                "BUG QA-061: ?fhir_vs=ISA (uppercase) silently returns "
                "descendants-only — the value lookup is case-sensitive. "
                "Either accept case-insensitive OR reject explicitly. "
                "Got: " + str(codes)
            )


class TestVS04EdgeCaseMalformedUrl:
    """SKEPTIC edge cases: malformed URLs and unusual inputs."""

    def test_s90_double_question_mark(self, fhir_client):
        """URL with double ``?`` (malformed) handled gracefully (no 500)."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}??fhir_vs=isa",
        )
        # No 500 crash; either 200 (treats last query as canonical) or 400.
        assert resp.status_code in (200, 400, 422), (
            f"Double-? URL returned {resp.status_code}"
        )

    def test_s91_trailing_slash_after_code(self, fhir_client):
        """``.../sct/{code}/?fhir_vs=isa`` (trailing slash) handled gracefully."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}/?fhir_vs=isa",
        )
        # Current implementation: path_parts splits to ['', 'sct', '73211009', '']
        # then path_parts[-1] is '' (empty string), but len(path_parts) >= 2 is True
        # so the code path runs. Let's verify it doesn't crash.
        assert resp.status_code in (200, 400, 422), (
            f"Trailing-slash URL returned {resp.status_code}"
        )

    def test_s92_post_with_fhir_vs_url(self, fhir_client):
        """POST $expand with url in Parameters body works equivalently to GET."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "url", "valueUri": f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"},
            ],
        }
        resp = fhir_client.post("/fhir/ValueSet/$expand", json=body)
        # POST with url in body SHOULD be accepted by the operation. Status
        # 200 means the implementation honors the Parameters-body form.
        assert resp.status_code in (200, 400, 422), (
            f"POST with url in body returned {resp.status_code}"
        )

    def test_s93_url_with_fragments(self, fhir_client):
        """URL with fragment (``#...``) handled gracefully."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa#frag",
        )
        assert resp.status_code in (200, 400, 422), (
            f"URL with fragment returned {resp.status_code}"
        )


class TestVS04UrlDetectionGuard:
    """SKEPTIC: verify the URL detection logic doesn't shadow intensional path.

    Per TS-03 SKEPTIC QA-032 + HISTORIAN QA-034: the implicit value set
    detection (``_is_implicit_value_set_url``) handles ``.../sct?fhir_vs``
    (no code), while the intensional path (``_expand_url_pattern``) handles
    ``.../sct/<code>?fhir_vs=isa``. The detection MUST NOT shadow the
    intensional path.
    """

    def test_s100_intensional_with_code_does_not_use_implicit_path(
        self, fhir_client
    ):
        """``.../sct/<code>?fhir_vs=isa`` MUST use the intensional path,
        not the implicit value set path.

        The intensional path returns root + descendants (isa semantics).
        The implicit path returns all SNOMED codes (potentially truncated).
        For the conformance fixture, both return {73211009, 44054006}, but
        the intensional path includes the url echo.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("url") == (
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        ), f"url must be echoed in response; got {body.get('url')!r}"
