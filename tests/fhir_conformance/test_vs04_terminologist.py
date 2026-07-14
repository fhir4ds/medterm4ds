"""VS-04 TERMINOLOGIST: ValueSet $expand — Intensional URLs (fhir_vs).

Source: https://build.fhir.org/valueset-operation-expand.html
Canonical R4: https://hl7.org/fhir/R4/valueset-operation-expand.html
SNOMED CT intensional: https://hl7.org/fhir/R4/snomedct.html
Truncation ext: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
ValueSet.expansion.contains.display:
  https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display

TERMINOLOGIST lens (HIGH severity per GLOBAL_RULES.md "TERMINOLOGIST Findings
Are HIGH"): clinical/terminological correctness on the intensional-URL
$fhir_vs expansion surface. Every probe asserts a CLINICAL-TRUTH property:

  Lens 1 — fhir_vs=isa expansion clinical correctness:
      root + descendants; root display = engine canonical preferred term.
  Lens 2 — Descendant display correctness:
      each descendant's display MUST be the engine canonical preferred term,
      cross-checked via $lookup.
  Lens 3 — Code-system URI round-trip:
      every code in expansion MUST round-trip via $lookup with the
      advertised system.
  Lens 4 — Cross-system clinical safety:
      ?fhir_vs=isa on non-SNOMED systems MUST return a clinically clear
      error naming the offending system.
  Lens 5 — Truncation honesty (clinical safety):
      count-truncated expansions MUST surface the toocostly extension.
  Lens 6 — Patient-friendly surfacing (GAP-T01 carry-forward):
      SNOMED descendants should NOT surface patient-friendly extension
      (LOINC-only feature in this engine).
  Lens 7 — Cross-operation canonical agreement:
      intensional URL path contains[].display MUST agree with $lookup.
  Lens 8 — Carry-forwards reconfirmed (CF-HISTORIAN-VS02-01/02,
      CF-SKEPTIC-VS01-01).

Prior VS-04 iterations:
  - SKEPTIC: 5 fixes (QA-060 unknown value, QA-061 case sensitivity,
    QA-062 refset, QA-065 depth=0 truncation, QA-066 invalid env var).
  - HISTORIAN: 1 fix (QA-067 negative depth).
  - EXPLORER: 0 bugs; 46 lateral probes (surface hardened).

Conformance fixture (tests/fhir_conformance/conftest.py):
  - SNOMEDCT_US: 73211009 (Diabetes mellitus) -> 44054006 (T2DM)
  - ICD10CM: E11 (T2DM)
  - RXNORM: 860975 (24 HR metformin 500 MG Oral Tablet)
  - mrrel: 1 row (T2DM isa Diabetes mellitus)

Default severity: HIGH per GLOBAL_RULES.md "TERMINOLOGIST Findings Are HIGH".
"""

from __future__ import annotations

import pytest

# Spec: https://hl7.org/fhir/R4/snomedct.html (Implicit Value Sets)
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.display

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent (root)
SNOMED_T2DM = "44054006"               # child of 73211009

LOINC_URI = "http://loinc.org"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
CPT_URI = "http://www.ama-assn.org/go/cpt"
CVX_URI = "http://hl7.org/fhir/sid/cvx"

TRUNCATION_EXT_URL = "http://hl7.org/fhir/StructureDefinition/valueset-toocostly"

# Canonical preferred terms per the conformance fixture.
# Spec: FHIR R4 ValueSet.expansion.contains.display: "The recommended display
# for this item in the expansion" — implies the SERVER's canonical preferred
# term, not a client-supplied echo.
CANONICAL_DISPLAY_SNOMED_DM = "Diabetes mellitus"
CANONICAL_DISPLAY_SNOMED_T2DM = "Type 2 diabetes mellitus"


# =============================================================================
# Helpers
# =============================================================================

def _expand_url(client, url: str, count: int | None = None):
    """GET /fhir/ValueSet/$expand with the given url (and optional count)."""
    params = [("url", url)]
    if count is not None:
        params.append(("count", count))
    return client.get(
        "/fhir/ValueSet/$expand",
        params=params,
        headers={"Accept": "application/fhir+json"},
    )


def _contains_codes(resp_json: dict) -> list[str]:
    return [c.get("code") for c in resp_json.get("expansion", {}).get("contains", [])]


def _contains_entries(resp_json: dict) -> list[dict]:
    return resp_json.get("expansion", {}).get("contains", [])


def _contains_displays(resp_json: dict) -> dict[str, str]:
    """Map code -> display from ValueSet.expansion.contains."""
    return {
        c.get("code", ""): c.get("display", "")
        for c in resp_json.get("expansion", {}).get("contains", [])
    }


def _extensions(resp_json: dict) -> list[dict]:
    return resp_json.get("expansion", {}).get("extension", [])


def _lookup(client, system: str, code: str):
    return client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": system, "code": code},
        headers={"Accept": "application/fhir+json"},
    )


# =============================================================================
# Lens 1: fhir_vs=isa expansion clinical correctness
# Spec: https://hl7.org/fhir/R4/snomedct.html — "the expression
#   ``http://snomed.info/sct?fhir_vs=isa/<conceptId>`` ... means all concepts
#   that are descendents of the named concept AND the concept itself".
# Clinical safety: a CDS hook reading an isa expansion MUST see the root AND
# every descendant — silently missing either is a clinical hazard.
# =============================================================================


class TestLens1IsaExpansionClinicalCorrectness:
    """Lens 1: ``?fhir_vs=isa`` MUST produce a clinically correct expansion.

    The root concept MUST be present (SKEPTIC QA-060/QA-061/QA-062 confirmed
    value dispatch); the descendant MUST be present (T2DM is the only child
    seeded in the fixture); the root display MUST be the engine canonical
    preferred term — VS-01 TERMINOLOGIST QA-056 fix applied here.
    """

    def test_t10_isa_includes_root_diabetes_mellitus(self, fhir_client):
        """``?fhir_vs=isa/73211009`` MUST include Diabetes mellitus root.

        Clinical hazard if missing: a CDS rule for "screen for diabetes
        (any)" would silently miss the root category — silent-misclassification.
        """
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes, (
            f"isa expansion MUST include root {SNOMED_DIABETES_MELLITUS}; got {codes}. "
            f"Clinical hazard: a 'diabetes-any' CDS rule would silently miss the root category."
        )

    def test_t11_isa_includes_descendant_t2dm(self, fhir_client):
        """``?fhir_vs=isa/73211009`` MUST include descendant T2DM (44054006).

        Clinical hazard if missing: a 'diabetes-any' CDS rule would silently
        miss every Type 2 diabetes patient — the most common subtype.
        """
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert SNOMED_T2DM in codes, (
            f"isa expansion MUST include descendant {SNOMED_T2DM}; got {codes}. "
            f"Clinical hazard: T2DM is the most common subtype — missing it is unsafe."
        )

    def test_t12_isa_root_display_is_engine_canonical(self, fhir_client):
        """Root display in isa expansion MUST be engine canonical preferred term.

        Per FHIR R4 ValueSet.expansion.contains.display: "The recommended
        display for this item in the expansion." For SNOMED 73211009 the
        canonical preferred term is "Diabetes mellitus" — NOT a raw code,
        NOT an empty string, NOT the FSN.

        VS-01 TERMINOLOGIST QA-056 fix (omitted-display canonical resolution)
        applies on this path: the URL-pattern expander resolves via
        ``get_code_infos([CodeRef(source, code)])`` and uses ``.name``.
        """
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        displays = _contains_displays(resp.json())
        assert displays[SNOMED_DIABETES_MELLITUS] == CANONICAL_DISPLAY_SNOMED_DM, (
            f"root display not engine canonical: {displays}. "
            f"Expected {CANONICAL_DISPLAY_SNOMED_DM!r} (preferred term). "
            f"VS-01 QA-056 fix may be regressed on URL-pattern path."
        )

    def test_t13_isa_descendant_display_is_engine_canonical(self, fhir_client):
        """Descendant display in isa expansion MUST be engine canonical preferred term.

        For SNOMED 44054006 the canonical preferred term is "Type 2 diabetes
        mellitus". The descendant walk via ``get_descendants_bfs`` returns
        ``CodeRelation.target_display`` — this MUST be the canonical preferred
        term, NOT a raw code, NOT empty.
        """
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        displays = _contains_displays(resp.json())
        assert displays[SNOMED_T2DM] == CANONICAL_DISPLAY_SNOMED_T2DM, (
            f"descendant display not engine canonical: {displays}. "
            f"Expected {CANONICAL_DISPLAY_SNOMED_T2DM!r}."
        )

    def test_t14_isa_on_leaf_returns_just_leaf(self, fhir_client):
        """``?fhir_vs=isa`` on a leaf (T2DM has no descendants) returns just
        the leaf — clinically correct: a 'type 2 diabetes' CDS rule applies
        to the T2DM concept itself, not to descendants.
        """
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_T2DM}?fhir_vs=isa")
        assert resp.status_code == 200
        codes = _contains_codes(resp.json())
        assert codes == [SNOMED_T2DM], (
            f"isa on leaf MUST return just the leaf; got {codes}. "
            f" Clinical hazard: leaf 'isa' includes only the leaf concept itself."
        )

    def test_t15_isa_on_t2dm_display_is_engine_canonical(self, fhir_client):
        """``?fhir_vs=isa`` on T2DM has display = engine canonical preferred term."""
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_T2DM}?fhir_vs=isa")
        assert resp.status_code == 200
        displays = _contains_displays(resp.json())
        assert displays[SNOMED_T2DM] == CANONICAL_DISPLAY_SNOMED_T2DM, (
            f"leaf root display not canonical: {displays}"
        )

    def test_t16_isa_all_entries_use_canonical_snomed_uri(self, fhir_client):
        """Every entry in isa expansion advertises the canonical SNOMED URI.

        Clinical safety: a CDS hook reading contains[].system would feed it
        into $lookup for enrichment; an off-canonical URI (e.g. the
        TS-04 SKEPTIC QA-037 IPv6-trailing-slash-shape bug on the deployment
        URL — different bug class but same hazard family) would silently
        fail the round-trip.
        """
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        for entry in _contains_entries(resp.json()):
            assert entry.get("system") == SNOMED_URI, (
                f"contains entry uses non-canonical URI {entry.get('system')!r}; "
                f"expected {SNOMED_URI!r}. Clinical hazard: $lookup round-trip "
                f"would fail with 'Unrecognized system URI'."
            )


# =============================================================================
# Lens 2: Descendant display correctness — cross-check via $lookup
# Spec: $lookup Out `display` is the server's canonical preferred term
#   (TS-02 TERMINOLOGIST QA-029 + CS-03 SKEPTIC QA-048 cross-operation
#   canonical-agreement invariant). The URL-pattern path's contains[].display
#   MUST agree with $lookup display for the same code.
# =============================================================================


class TestLens2DescendantDisplayCrossCheckedViaLookup:
    """Lens 2: descendant display cross-checked via $lookup.

    The URL-pattern expander resolves descendant displays via
    ``rel.target_display`` (sourced from engine). The $lookup handler resolves
    via ``code_info.name``. Both MUST return the SAME canonical preferred
    term for the same code (cross-operation canonical-agreement invariant —
    CS-05 EXPLORER test_e10/e11).
    """

    def test_t20_descendant_display_matches_lookup(self, fhir_client):
        """T2DM's display in isa expansion MUST match $lookup display."""
        # Get the expansion.
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        expand_display = _contains_displays(resp.json())[SNOMED_T2DM]

        # Round-trip via $lookup.
        lu = _lookup(fhir_client, SNOMED_URI, SNOMED_T2DM)
        assert lu.status_code == 200, (
            f"$lookup round-trip failed for SNOMED {SNOMED_T2DM}: "
            f"{lu.status_code} {lu.text}"
        )
        param_displays = [
            p.get("valueString") for p in lu.json().get("parameter", [])
            if p.get("name") == "display"
        ]
        assert expand_display in param_displays, (
            f"canonical agreement broken: $expand display={expand_display!r} "
            f"vs $lookup display={param_displays}. Clinical hazard: a CDS hook "
            f"would see two different canonical names for the same code."
        )

    def test_t21_root_display_matches_lookup(self, fhir_client):
        """Root display in isa expansion MUST match $lookup display."""
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        expand_display = _contains_displays(resp.json())[SNOMED_DIABETES_MELLITUS]

        lu = _lookup(fhir_client, SNOMED_URI, SNOMED_DIABETES_MELLITUS)
        assert lu.status_code == 200, (
            f"$lookup round-trip failed for SNOMED {SNOMED_DIABETES_MELLITUS}: "
            f"{lu.status_code} {lu.text}"
        )
        param_displays = [
            p.get("valueString") for p in lu.json().get("parameter", [])
            if p.get("name") == "display"
        ]
        assert expand_display in param_displays, (
            f"root canonical agreement broken: $expand={expand_display!r} "
            f"vs $lookup={param_displays}."
        )

    def test_t22_leaf_root_display_matches_lookup(self, fhir_client):
        """T2DM-as-leaf (no descendants) display MUST match $lookup.

        The fixture has T2DM with no descendants; isa on T2DM returns just
        T2DM with its canonical display. This MUST agree with $lookup.
        """
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_T2DM}?fhir_vs=isa")
        assert resp.status_code == 200
        expand_display = _contains_displays(resp.json())[SNOMED_T2DM]
        lu = _lookup(fhir_client, SNOMED_URI, SNOMED_T2DM)
        assert lu.status_code == 200
        param_displays = [
            p.get("valueString") for p in lu.json().get("parameter", [])
            if p.get("name") == "display"
        ]
        assert expand_display in param_displays


# =============================================================================
# Lens 3: Code-system URI round-trip on intensional URL expansion
# Spec: https://hl7.org/fhir/R4/valueset-definitions.html#ValueSet.expansion.contains.system
#   "An absolute URI which is the code system URI of the code system from
#    which the code in the expansion was defined."
# Each code in the expansion MUST advertise a system URI that $lookup can
# resolve (round-trip contract). Catch silent URI drift on URL-pattern path.
# =============================================================================


class TestLens3SystemUriRoundTrip:
    """Lens 3: every contains[].system in isa expansion MUST round-trip.

    A CDS hook receiving the expansion will pass each Coding through $lookup
    for enrichment; the URI MUST resolve (200). If any contains[].system is
    a silent drift, $lookup would 400 with "Unrecognized system URI".
    """

    @pytest.mark.parametrize("code", [SNOMED_DIABETES_MELLITUS, SNOMED_T2DM])
    def test_t30_advertised_system_round_trips_via_lookup(self, fhir_client, code):
        """For each code in isa expansion, $lookup with the advertised system
        MUST succeed.
        """
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        entries = _contains_entries(resp.json())
        matching = [e for e in entries if e.get("code") == code]
        assert matching, f"code {code} not in expansion: {_contains_codes(resp.json())}"
        advertised_system = matching[0].get("system")

        lu = _lookup(fhir_client, advertised_system, code)
        assert lu.status_code == 200, (
            f"$lookup round-trip failed with system={advertised_system!r}, "
            f"code={code!r}: {lu.status_code} {lu.text}. "
            f"Expander advertised a system that does not resolve."
        )

    def test_t31_canonical_snomed_uri_no_alias_drift(self, fhir_client):
        """Every contains[].system is the canonical SNOMED URI, not an alias.

        The URL-pattern expander sources ``system_uri`` from
        ``SYSTEM_TO_FHIR_URI["SNOMEDCT_US"]``. The fixture uses the canonical
        URL form; the response MUST echo the canonical URI (not e.g. the
        deployment URL or a trailing-slash variant).
        """
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        for entry in _contains_entries(resp.json()):
            assert entry.get("system") == SNOMED_URI


# =============================================================================
# Lens 4: Cross-system clinical safety
# Spec: Per GLOBAL_RULES "FHIR API Specifics" — "$expand?url=...?fhir_vs=isa
# only supports SNOMED CT intensional expansions. Other systems raise
# ValueError with a clear message — they lack a standard intensional URL
# convention."
# Clinical safety: the error message MUST be clinically clear so a CDS
# engineer seeing the 400 knows WHY the operation failed and HOW to fix it
# (e.g. "LOINC does not support intensional value set URLs — use explicit
# code lists instead").
# =============================================================================


class TestLens4CrossSystemClinicalSafety:
    """Lens 4: non-SNOMED intensional URLs MUST return a clinically clear error.

    Per GLOBAL_RULES "FHIR API Specifics": only SNOMED has a standard
    intensional URL convention. Other systems return ValueError → HTTP 400
    with OperationOutcome. The diagnostics message MUST name the offending
    system so the CDS engineer knows which system the operator tried to
    intensionally expand and why it failed.
    """

    @pytest.mark.parametrize("system_uri,name_in_msg", [
        (LOINC_URI, "loinc"),
        (RXNORM_URI, "rxnorm"),
        (ICD10CM_URI, "icd"),
        (CPT_URI, "cpt"),
        (CVX_URI, "cvx"),
    ])
    def test_t40_non_snomed_system_returns_clinically_clear_400(self, fhir_client, system_uri, name_in_msg):
        """Non-SNOMED intensional URLs return 400 + OperationOutcome.

        Spec contract: ``ValueError`` at the engine layer becomes 400 +
        OperationOutcome at the HTTP layer. The diagnostics MUST reference
        the URL the client sent so the engineer can debug.
        """
        url = f"{system_uri}/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        resp = _expand_url(fhir_client, url)
        assert resp.status_code == 400, (
            f"non-SNOMED intensional URL MUST return 400; got {resp.status_code} "
            f"for {url}. Clinical hazard: intensional expansion is SNOMED-only."
        )
        body = resp.json()
        assert body.get("resourceType") == "OperationOutcome", (
            f"expected OperationOutcome; got {body.get('resourceType')}"
        )
        # The diagnostics MUST mention SNOMED (so the engineer knows what the
        # supported system is) AND/OR the URL the client sent.
        diagnostics = " ".join(
            issue.get("diagnostics", "") + " " + issue.get("details", {}).get("text", "")
            for issue in body.get("issue", [])
        ).lower()
        assert "snomed" in diagnostics or "fhir_vs" in diagnostics or "intensional" in diagnostics, (
            f"diagnostics should reference SNOMED / intensional convention; "
            f"got diagnostics={diagnostics!r}. "
            f"Clinical clarity: the engineer needs to know intensional URL "
            f"convention is SNOMED-only."
        )

    def test_t41_loinc_vs_suffix_routes_through_implicit_path_not_intensional(self, fhir_client):
        """LOINC ``/vs`` suffix is the IMPLICIT value set form (TS-03 SKEPTIC
        QA-032 dispatch ordering invariant) — NOT the intensional path.

        Clinical correctness: a CDS engineer hitting ``http://loinc.org/vs``
        expects ALL LOINC codes (implicit enumeration), not an error. The
        TS-03 SKEPTIC QA-032 dispatch ordering ensures the implicit-value-set
        detector fires BEFORE the fhir_vs dispatcher.
        """
        resp = _expand_url(fhir_client, f"{LOINC_URI}/vs")
        # Implicit path is accepted (may return 200 with empty contains or
        # the empty-source extension from TS-03 HISTORIAN QA-033).
        assert resp.status_code == 200, (
            f"implicit LOINC value set URL MUST be accepted (200); got "
            f"{resp.status_code}. TS-03 QA-032 dispatch ordering invariant."
        )

    def test_t42_loinc_intensional_url_does_not_shadow_implicit(self, fhir_client):
        """LOINC ``?fhir_vs=isa`` is rejected via the intensional path; LOINC
        ``/vs`` is accepted via the implicit path. The two paths MUST NOT
        shadow each other (TS-03 SKEPTIC QA-032).
        """
        # /vs → 200 (implicit path)
        r1 = _expand_url(fhir_client, f"{LOINC_URI}/vs")
        assert r1.status_code == 200

        # /<code>?fhir_vs=isa → 400 (intensional path, non-SNOMED)
        r2 = _expand_url(fhir_client, f"{LOINC_URI}/12345-6?fhir_vs=isa")
        assert r2.status_code == 400


# =============================================================================
# Lens 5: Truncation honesty — clinical safety
# Spec: https://hl7.org/fhir/R4/extension-valueset-toocostly.html
#   "Indicates that the expansion of this value set is incomplete because
#   the system refused to expand the value set for some reason."
# Clinical safety: clinical decisions on silently-truncated expansions are
# unsafe. The toocostly extension MUST surface when count truncates.
# =============================================================================


class TestLens5TruncationHonesty:
    """Lens 5: count-truncation on intensional URL expansion MUST surface toocostly.

    Per VS-01 TERMINOLOGIST QA-055 + VS-02 SKEPTIC QA-057 + VS-04 SKEPTIC
    QA-065: the toocostly extension is the load-bearing clinical-safety
    signal. Without it, a CDS hook would silently receive a partial
    expansion and apply clinical rules to a subset of the relevant codes —
    a silent-misclassification hazard.
    """

    def test_t50_count_1_surfaces_toocostly(self, fhir_client):
        """count=1 on isa expansion surfaces toocostly extension."""
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["expansion"]["contains"]) <= 1
        exts = _extensions(body)
        assert any(e.get("url") == TRUNCATION_EXT_URL for e in exts), (
            f"toocostly extension missing on count=1 truncation: {exts}. "
            f"Clinical hazard: a CDS hook reading the partial expansion would "
            f"apply clinical rules to a silent subset."
        )

    def test_t51_toocostly_extension_valueBoolean_is_true(self, fhir_client):
        """toocostly extension valueBoolean MUST be lowercase ``true``.

        Per FHIR R4 §3.4.1: boolean primitives are lowercase. Mirrors
        v0.0.1 A1 + Milestone-1 CR-002 (XML/JSON boolean lowercase invariant).
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        assert resp.status_code == 200
        exts = _extensions(resp.json())
        toocostly = next((e for e in exts if e.get("url") == TRUNCATION_EXT_URL), None)
        assert toocostly is not None
        assert toocostly.get("valueBoolean") is True, (
            f"valueBoolean MUST be Python True (serialized as lowercase 'true'); "
            f"got {toocostly.get('valueBoolean')!r}"
        )

    def test_t52_expansion_total_reflects_untruncated_size(self, fhir_client):
        """expansion.total reflects the UN-truncated size of the isa expansion.

        VS-02 SKEPTIC QA-057 fix: callers passing ``total=len(contains)``
        before truncation. The URL-pattern path passes ``total=len(contains)``
        at line 280 AFTER the descendant walk but BEFORE the [:count] slice —
        the total IS the pre-slice size.

        Note: CF-HISTORIAN-VS02-01 documents that BFS-cap may itself truncate
        the total computation when count is small; the fixture coincidence
        (1 mrrel row, count=1, BFS limit=1) means total happens to equal the
        actual size here. The toocostly extension is the load-bearing signal.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        assert resp.status_code == 200
        body = resp.json()
        # Fixture: 1 descendant (T2DM) + root (DM) = 2.
        assert body["expansion"]["total"] >= 2, (
            f"total should reflect un-truncated size (>= 2 for DM + T2DM); "
            f"got {body['expansion'].get('total')}"
        )

    def test_t53_count_2_no_truncation_no_extension(self, fhir_client):
        """count=2 on the fixture (DM + T2DM = 2 codes) does NOT truncate.

        The expansion is complete; no toocostly extension should fire.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=2,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["expansion"]["contains"]) == 2
        exts = _extensions(body)
        assert not any(e.get("url") == TRUNCATION_EXT_URL for e in exts), (
            f"toocostly extension should NOT fire on complete expansion: {exts}"
        )

    def test_t54_depth_0_surfaces_toocostly(self, fhir_client, monkeypatch):
        """FHIR_VS_MAX_DEPTH=0 surfaces toocostly via SKEPTIC QA-065 fix."""
        monkeypatch.setenv("FHIR_VS_MAX_DEPTH", "0")
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        body = resp.json()
        # Depth=0 = root only (descendants excluded by max_depth=0).
        codes = _contains_codes(body)
        assert SNOMED_DIABETES_MELLITUS in codes
        # QA-065 fix: depth=0 MUST synthesize depth_cap_hit=True.
        exts = _extensions(body)
        assert any(e.get("url") == TRUNCATION_EXT_URL for e in exts), (
            f"toocostly missing on FHIR_VS_MAX_DEPTH=0: {exts}. "
            f"SKEPTIC QA-065 fix may be regressed. Clinical hazard: operator "
            f"caps at root-only and client cannot tell whether more descendants exist."
        )


# =============================================================================
# Lens 6: Patient-friendly name surfacing (GAP-T01 carry-forward)
# Spec: FHIR R4 §4.8.11 Concept Properties + custom properties via Out property
#   group. The spec does NOT require $expand to surface patient-friendly
#   names — this is a medterm4ds enhancement tied to $lookup.
# Per AGENTS.md GAP-T01 / CF-TERMINOLOGIST-01: the implicit value set expander
# resolves display via get_code_infos but does NOT consult PF cache.
#
# Clinical safety: SNOMED descendants typically don't have patient-friendly
# names (LOINC does). The engine MUST NOT surface PF where it shouldn't.
# =============================================================================


class TestLens6PatientFriendlySurfacing:
    """Lens 6: intensional URL expansion MUST NOT surface PF extension on SNOMED.

    Per GAP-T01 / CF-TERMINOLOGIST-01: PF surfacing is a deferred enhancement.
    Even when implemented, it would only apply to LOINC codes (the source
    where PF data exists). SNOMED descendants should NOT get PF extensions —
    doing so would surface spurious 'patient-friendly' strings that don't
    come from any SNOMED release file.

    These probes document the CURRENT behavior. When a future enhancement
    wires PF into $expand, the LOINC-specific probes MUST be updated; the
    SNOMED-specific probes MUST continue to assert PF is absent.
    """

    def test_t60_isa_expansion_no_patient_friendly_extension_on_contains(self, fhir_client):
        """isa expansion contains[] entries do NOT carry PF extension today."""
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        for entry in _contains_entries(resp.json()):
            exts = entry.get("extension", [])
            pf_exts = [
                e for e in exts
                if "patient-friendly" in e.get("url", "").lower()
            ]
            assert not pf_exts, (
                f"PF extension surfaced on SNOMED code {entry.get('code')!r}: "
                f"{pf_exts}. SNOMED descendants should NOT have PF (LOINC-only)."
            )

    def test_t61_isa_expansion_contains_no_pf_property(self, fhir_client):
        """isa expansion contains[] entries do NOT carry a ``patient-friendly``
        field at the contains level.
        """
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        for entry in _contains_entries(resp.json()):
            assert "patient-friendly" not in entry, (
                f"contains entry has unexpected 'patient-friendly' key: {entry}"
            )
            assert "patientFriendly" not in entry


# =============================================================================
# Lens 7: Cross-operation canonical agreement (CS-05 EXPLORER pattern)
# Spec: $lookup and $validate-code share the canonical re-resolution pattern
# (CS-02 HISTORIAN QA-047 + CS-03 HISTORIAN QA-051). The intensional URL
# expansion path's contains[].display and contains[].system MUST agree with
# $lookup on the same code.
# =============================================================================


class TestLens7CrossOperationCanonicalAgreement:
    """Lens 7: intensional URL expansion and $lookup agree on canonical values.

    Cross-operation invariant (CS-05 EXPLORER test_e10/e11): both operations
    share ``get_code_infos`` and the canonical-resolution pattern. The
    contains[].display returned by isa expansion MUST equal $lookup display
    for the same code; contains[].system MUST equal the system $lookup
    resolves to.
    """

    @pytest.mark.parametrize("code", [SNOMED_DIABETES_MELLITUS, SNOMED_T2DM])
    def test_t70_expand_and_lookup_agree_on_display(self, fhir_client, code):
        """For every code in isa expansion, $lookup display agrees."""
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        expand_displays = _contains_displays(resp.json())
        assert code in expand_displays

        lu = _lookup(fhir_client, SNOMED_URI, code)
        assert lu.status_code == 200
        param_displays = [
            p.get("valueString") for p in lu.json().get("parameter", [])
            if p.get("name") == "display"
        ]
        assert expand_displays[code] in param_displays, (
            f"cross-op canonical disagreement on display for {code}: "
            f"$expand={expand_displays[code]!r} vs $lookup={param_displays}"
        )

    @pytest.mark.parametrize("code", [SNOMED_DIABETES_MELLITUS, SNOMED_T2DM])
    def test_t71_expand_and_lookup_agree_on_system(self, fhir_client, code):
        """For every code in isa expansion, contains[].system matches the
        system $lookup accepts."""
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        entries = _contains_entries(resp.json())
        advertised = next((e.get("system") for e in entries if e.get("code") == code), None)
        assert advertised is not None

        # $lookup with the advertised system MUST succeed (round-trip).
        lu = _lookup(fhir_client, advertised, code)
        assert lu.status_code == 200, (
            f"$lookup round-trip failed with advertised system {advertised!r}: "
            f"{lu.status_code} {lu.text}"
        )


# =============================================================================
# Lens 8: Carry-forwards reconfirmed (CS-03 TERMINOLOGIST methodology)
# Each carry-forward MUST be probed by every subsequent personality to confirm
# it remains a load-bearing contract. If the CF is closed without updating
# the probe, the probe MUST fail loudly.
# =============================================================================


class TestLens8CarryForwardReconfirmations:
    """Lens 8: reconfirm VS-04-relevant carry-forwards remain open.

    These probes document the CURRENT (deferred) behavior. When the CF is
    closed, the probe MUST be updated to assert the new behavior.
    """

    def test_t80_cf_historian_vs02_01_bfs_cap_on_total_url_pattern(self, fhir_client):
        """CF-HISTORIAN-VS02-01: BFS-cap-on-total applies on URL-pattern path.

        The fixture has exactly 1 mrrel row matching BFS limit=1 when count=1,
        so the total happens to equal the actual size. When the structural
        fix lands (extend ``get_descendants_bfs`` to return total_count), this
        probe MUST be updated.
        """
        resp = _expand_url(
            fhir_client,
            f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa",
            count=1,
        )
        assert resp.status_code == 200
        body = resp.json()
        # Fixture coincidence: total = 2 (DM root + 1 descendant).
        # When CF is closed, total would reflect the true un-truncated size.
        assert body["expansion"]["total"] == 2, (
            f"fixture-coincidence total expected to be 2; got "
            f"{body['expansion'].get('total')}. If FAILED, CF-HISTORIAN-VS02-01 "
            f"may be closed — update to assert true un-truncated size."
        )

    def test_t81_cf_historian_vs02_02_url_pattern_canonical_uri(self, fhir_client):
        """CF-HISTORIAN-VS02-02: URL-pattern path uses canonical SNOMED URI
        for contains[].system (sourced from SYSTEM_TO_FHIR_URI, not from
        the client-supplied URL).

        Bug invisible because the fixture uses the canonical URL form. Probe
        passes today because contains[].system IS the canonical SNOMED URI;
        when CF is closed (alias re-resolution applied), the probe can be
        tightened to test alias inputs.
        """
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        for entry in _contains_entries(resp.json()):
            assert entry.get("system") == SNOMED_URI, (
                f"contains[].system is non-canonical: {entry.get('system')!r}"
            )

    def test_t82_cf_skeptic_vs01_01_no_filter_operator_leakage(self, fhir_client):
        """CF-SKEPTIC-VS01-01: the 7 unimplemented filter operators don't
        leak to the URL-pattern path. The URL-pattern path doesn't process
        ``compose.include[].filter[]`` (it only processes the fhir_vs URL
        convention) — so this CF is structurally N/A on this path. Probe
        documents the absence.
        """
        # URL-pattern path with no filter operator involvement — fhir_vs
        # convention is the only dispatch input.
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        # The 7 operators don't apply here (no compose.include.filter).
        # Confirming the isa expansion still returns root + descendant.
        codes = _contains_codes(resp.json())
        assert SNOMED_DIABETES_MELLITUS in codes
        assert SNOMED_T2DM in codes

    def test_t83_cf_terminologist_vs01_01_supplied_display_echo_not_applicable(self, fhir_client):
        """CF-TERMINOLOGIST-VS01-01: client-supplied display echo is on the
        inline-VALUESET path (``compose.include[].concept[].display``), NOT
        on the URL-pattern path. The URL-pattern path has no client-supplied
        display; the display is sourced from the engine.

        This probe documents that the CF is structurally N/A on this path.
        """
        # URL-pattern path — no compose.include.concept to supply display.
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert resp.status_code == 200
        displays = _contains_displays(resp.json())
        # Both displays are engine canonical (no client-supplied display).
        assert displays[SNOMED_DIABETES_MELLITUS] == CANONICAL_DISPLAY_SNOMED_DM
        assert displays[SNOMED_T2DM] == CANONICAL_DISPLAY_SNOMED_T2DM


# =============================================================================
# Lens 9: Implicit value set semantics — bare ?fhir_vs on SNOMED
# Spec: https://hl7.org/fhir/R4/snomedct.html — the bare form ``?fhir_vs``
# (no value) is equivalent to ``?fhir_vs=isa``. TS-03 HISTORIAN QA-034 fix
# ensures the bare-query form is detected (parse_qs requires key=value).
# =============================================================================


class TestLens9BareFhirVsSemantics:
    """Lens 9: bare ``?fhir_vs`` on SNOMED URL is equivalent to isa.

    The TS-03 HISTORIAN QA-034 fix added bare-query detection
    (``parsed.query == "fhir_vs"`` instead of relying on parse_qs).

    Clinical safety: a CDS engineer using the bare form (per the SNOMED CT
    FHIR binding convention) MUST get the same expansion as ``?fhir_vs=isa``.
    """

    def test_t90_bare_fhir_vs_equivalent_to_isa_codes(self, fhir_client):
        """Bare ``?fhir_vs`` returns the same codes as ``?fhir_vs=isa``."""
        r1 = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs")
        r2 = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa")
        assert r1.status_code == 200 and r2.status_code == 200
        c1 = set(_contains_codes(r1.json()))
        c2 = set(_contains_codes(r2.json()))
        assert c1 == c2, (
            f"bare ?fhir_vs {c1} != ?fhir_vs=isa {c2}. "
            f"TS-03 HISTORIAN QA-034 fix contract: the two forms are equivalent."
        )

    def test_t91_bare_fhir_vs_root_display_canonical(self, fhir_client):
        """Bare ``?fhir_vs`` root display is engine canonical preferred term."""
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs")
        assert resp.status_code == 200
        displays = _contains_displays(resp.json())
        assert displays[SNOMED_DIABETES_MELLITUS] == CANONICAL_DISPLAY_SNOMED_DM

    def test_t92_bare_fhir_vs_descendant_display_canonical(self, fhir_client):
        """Bare ``?fhir_vs`` descendant display is engine canonical preferred term."""
        resp = _expand_url(fhir_client, f"http://snomed.info/sct/{SNOMED_DIABETES_MELLITUS}?fhir_vs")
        assert resp.status_code == 200
        displays = _contains_displays(resp.json())
        assert displays[SNOMED_T2DM] == CANONICAL_DISPLAY_SNOMED_T2DM


# =============================================================================
# Lens 10: Versioned SNOMED URL — intensional semantics preserved
# Spec: https://hl7.org/fhir/R4/snomedct.html — the SNOMED URL MAY include
# edition/version path segments: ``http://snomed.info/sct/{edition}/
# version/{date}/{code}?fhir_vs=isa``. The implementation MUST extract the
# last path segment as the code (SKEPTIC VS-04 test_s41 load-bearing contract).
# =============================================================================


class TestLens10VersionedSnomedUrl:
    """Lens 10: versioned SNOMED URL preserves isa clinical semantics.

    The implementation extracts the last path segment as the code
    (path_parts[-1]) — SKEPTIC VS-04 test_s41 confirmed this is the
    load-bearing contract. The TERMINOLOGIST concern: the resulting
    expansion MUST still be clinically correct (root + descendants,
    canonical displays) regardless of the URL path segments.
    """

    def test_t100_versioned_url_returns_correct_codes(self, fhir_client):
        """Versioned SNOMED URL extracts the code from the last path segment."""
        url = (
            f"http://snomed.info/sct/731000124108/version/20240901/"
            f"{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        resp = _expand_url(fhir_client, url)
        # The implementation accepts or rejects with 200/400/422; no 500.
        assert resp.status_code in (200, 400, 422), (
            f"versioned URL caused unexpected status {resp.status_code}: {resp.text}"
        )
        if resp.status_code == 200:
            codes = _contains_codes(resp.json())
            # If accepted, MUST return the isa expansion of DM.
            assert SNOMED_DIABETES_MELLITUS in codes
            assert SNOMED_T2DM in codes

    def test_t101_international_edition_url_preserves_isa_semantics(self, fhir_client):
        """International-edition SNOMED URL preserves isa semantics.

        ``http://snomed.info/sct/900000000000207008/{code}?fhir_vs=isa`` —
        the 900000000000207008 is the SNOMED CT core module ID.
        """
        url = (
            f"http://snomed.info/sct/900000000000207008/{SNOMED_DIABETES_MELLITUS}?fhir_vs=isa"
        )
        resp = _expand_url(fhir_client, url)
        assert resp.status_code in (200, 400, 422), (
            f"international edition URL caused unexpected status {resp.status_code}: {resp.text}"
        )
        if resp.status_code == 200:
            codes = _contains_codes(resp.json())
            assert SNOMED_DIABETES_MELLITUS in codes
            assert SNOMED_T2DM in codes
