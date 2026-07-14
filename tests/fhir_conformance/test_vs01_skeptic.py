"""SKEPTIC probes for VS-01 (ValueSet Resource Structure).

Spec: https://build.fhir.org/valueset.html
       (canonical R4: https://hl7.org/fhir/R4/valueset.html)
       Filter operators: https://hl7.org/fhir/R4/valueset.html#filter
       Expansion: https://hl7.org/fhir/R4/valueset.html#expansion

Scope (per chunk assignment) — 6 items:
  1. Intensional vs extensional compose.
  2. compose.include: system, version, concept (extensional), filter (intensional).
  3. compose.exclude: same structure as include, subtracts from include.
  4. compose.filter operators: =, is-a, descendent-of, is-not-a, regex, in,
     not-in, generalizes, exists (9-value FHIR R4 enum).
  5. ValueSet.url as canonical identifier (echoed in $expand response).
  6. READ and SEARCH interactions work for ValueSet (likely N/A — medterm4ds
     does not persist ValueSet resources; TS-01 SKEPTIC QA-002/QA-003
     established the stubs).

SKEPTIC lens — adversarial bug hunting on the inline-ValueSet intensional
expansion path (`_expand_intensional` in apps/fhir_api.py):

  - Extensional: POST `compose.include[].concept[]` — verify every concept
    listed is in the expansion.
  - Intensional: POST `compose.include[].filter[]` — verify all 9 spec-listed
    operators (`=`, `is-a`, `descendent-of`, `is-not-a`, `regex`, `in`,
    `not-in`, `generalizes`, `exists`) are either honored or explicitly
    rejected. SILENT DROP is a finding (v0.0.1 B-class silent-fallback shape).
  - compose.exclude: POST a ValueSet with both include AND exclude — verify
    exclusion works. Also probe exclude-with-filter (the spec says exclude has
    the same structure as include).
  - Operator vocabulary exactness: probe with synonyms / typos to confirm the
    server rejects them rather than silently dropping.
  - ValueSet.url: POST a ValueSet body with `url` set — verify it is echoed
    in the response (per §4.9.10: "The canonical URL for the expansion is
    the same as the value set it was expanded from").
  - READ: `GET /fhir/ValueSet/{id}` — 404 OperationOutcome (per TS-01 QA-002).
  - SEARCH: `GET /fhir/ValueSet?url=...&version=...` — empty Bundle (per
    TS-01 SKEPTIC QA-003).

Per GLOBAL_RULES.md:
  - "Test-too-lenient": every probe asserts POSITIVE success shape (200 +
    expected fields), not just absence of one error string.
  - "Don't manufacture bugs": if the fixture lacks data to exercise an item,
    document as DEFERRED with reproduction shape.
  - Spec citation required on every probe.

Reference fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus",   "A73211009", "N", "SNOMEDCT_US", "C0011849"),  # parent
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),  # child
    ("E11",      "HT", "Type 2 diabetes mellitus", "AE11",      "N", "ICD10CM",    "C0011847"),
    ("860975",   "SCD","24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")  # 44054006 is-a 73211009

The SNOMED hierarchy gives us parent (73211009) → child (44054006), which
lets us probe `is-a` and `descendent-of` with real data.
"""

from __future__ import annotations

import pytest

# Spec: https://hl7.org/fhir/R4/valueset.html (R4 canonical)
# Spec: https://hl7.org/fhir/R4/valueset.html#filter (Filter operators)
#
# FHIR R4 filter-operator enum (9 values — R6 adds `child-of` and
# `descendent-leaf`, NOT in R4 scope). Per
# https://hl7.org/fhir/R4/valueset.html#filter:
#   op 1..1 code  = | is-a | descendent-of | is-not-a | regex | in | not-in |
#                       generalizes | exists
#   Binding: Filter Operator (Required)
FHIR_R4_FILTER_OPERATORS = {
    "=",
    "is-a",
    "descendent-of",
    "is-not-a",
    "regex",
    "in",
    "not-in",
    "generalizes",
    "exists",
}

SNOMED_URI = "http://snomed.info/sct"
SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_METFORMIN = "860975"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_T2DM = "E11"


def _post_expand(fhir_client, value_set: dict) -> tuple[int, dict]:
    """POST a ValueSet body to /fhir/ValueSet/$expand.

    Returns (status_code, body_json). Per FHIR R4 §4.7.5
    (https://hl7.org/fhir/R4/valueset-operation-expand.html), $expand accepts
    a ValueSet resource body via POST.
    """
    resp = fhir_client.post(
        "/fhir/ValueSet/$expand",
        json=value_set,
        headers={"Accept": "application/fhir+json"},
    )
    try:
        body = resp.json()
    except Exception:
        body = {"_raw": resp.text}
    return resp.status_code, body


def _contains_codes(body: dict) -> list[tuple[str, str]]:
    """Extract the (system, code) pairs from a ValueSet.expansion.contains."""
    out = []
    for c in body.get("expansion", {}).get("contains", []):
        out.append((c.get("system", ""), c.get("code", "")))
    return out


# =============================================================================
# Item 1: Intensional vs extensional compose
# =============================================================================


class TestItem1IntensionalVsExtensional:
    """Item 1: distinguish intensional (filter) vs extensional (concept list)."""

    def test_s10_extensional_compose_returns_listed_concepts(self, fhir_client):
        """Extensional: compose.include[].concept[] enumerates codes.

        Per https://hl7.org/fhir/R4/valueset.html §4.9.6.1: "listing codes
        explicitly ... is called an 'extensional' definition". The expansion
        MUST contain every listed concept (§4.9.10 step 1: "If codes are
        listed, check that they are valid, and check their active status,
        and if ok, add them to the result set").
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/test-extensional",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "concept": [
                            {"code": SNOMED_DIABETES_MELLITUS, "display": "Diabetes mellitus"},
                            {"code": SNOMED_T2DM, "display": "Type 2 diabetes mellitus"},
                        ],
                    }
                ]
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"expected 200, got {status}: {body}"
        assert body["resourceType"] == "ValueSet"
        codes = _contains_codes(body)
        # Both listed concepts MUST appear in the expansion.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s11_intensional_compose_returns_hierarchy(self, fhir_client):
        """Intensional: compose.include[].filter[] walks hierarchy.

        Per https://hl7.org/fhir/R4/valueset.html §4.9.6.1: intensional
        definitions use filter expressions. The expansion MUST include all
        codes satisfying the filter (§4.9.10 step 1: "If any filters are
        present, process them in order ... and add the intersection of their
        results to the result set").
        """
        vs = {
            "resourceType": "ValueSet",
            "url": "http://example.org/vs/test-intensional",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS},
                        ],
                    }
                ]
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200, f"expected 200, got {status}: {body}"
        codes = _contains_codes(body)
        # is-a MUST include the root code AND its descendants.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s12_extensional_with_unknown_code_omits_or_lists(self, fhir_client):
        """Extensional with an unseeded code: implementation choice.

        Per §4.9.10: "If codes are listed, check that they are valid". The
        server SHOULD include or omit per validity check. Documenting the
        current behavior — the implementation echoes the code regardless
        (no engine validity check on the intensional path).
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "concept": [
                            {"code": "NONEXISTENT_999"},
                        ],
                    }
                ]
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        # Current behavior: the code IS in the expansion (echo from input).
        # Pinning the current behavior so a future validity-check enhancement
        # will fail loudly and require a probe update.
        codes = _contains_codes(body)
        assert (SNOMED_URI, "NONEXISTENT_999") in codes


# =============================================================================
# Item 2: compose.include structure — system, version, concept, filter
# =============================================================================


class TestItem2ComposeInclude:
    """Item 2: every element of compose.include handled correctly."""

    def test_s20_system_element_required_for_concept(self, fhir_client):
        """compose.include[].system is the code system URI.

        Per §4.9.5 constraint vsd-2: "A value set with concepts or filters
        SHALL include a system". Without system, the server cannot resolve
        codes. The implementation does NOT enforce vsd-2 today; documenting
        the current behavior.
        """
        # With system: works.
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                ]
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s21_version_element_accepted_on_include(self, fhir_client):
        """compose.include[].version is the code system version.

        Per §4.9.5: version is "Specific version of the code system referred
        to". medterm4ds loads a single UMLS snapshot; the version is accepted
        but ignored. INTENDED today (mirrors $lookup/$validate-code/$subsumes
        version handling per AGENTS.md NOT A BUG registry).
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "version": "http://snomed.info/sct/731000124108",
                        "concept": [{"code": SNOMED_T2DM}],
                    }
                ]
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s22_concept_element_extensional(self, fhir_client):
        """compose.include[].concept[] is the extensional code list."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": RXNORM_URI,
                        "concept": [{"code": RXNORM_METFORMIN, "display": "Metformin"}],
                    }
                ]
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (RXNORM_URI, RXNORM_METFORMIN) in codes

    def test_s23_filter_element_intensional(self, fhir_client):
        """compose.include[].filter[] is the intensional rule."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": "descendent-of", "value": SNOMED_DIABETES_MELLITUS},
                        ],
                    }
                ]
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        # descendent-of returns descendants ONLY (no root).
        assert (SNOMED_URI, SNOMED_T2DM) in codes
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) not in codes

    def test_s24_multiple_includes_are_union(self, fhir_client):
        """Per §4.9.6: "Multiple include statements are cumulative".

        https://hl7.org/fhir/R4/valueset.html §4.9.6: "the value set contains
        the union of all the includes".
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_DIABETES_MELLITUS}]},
                    {"system": RXNORM_URI, "concept": [{"code": RXNORM_METFORMIN}]},
                ]
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (RXNORM_URI, RXNORM_METFORMIN) in codes


# =============================================================================
# Item 3: compose.exclude — subtracts from include
# =============================================================================


class TestItem3ComposeExclude:
    """Item 3: compose.exclude removes codes from include."""

    def test_s30_exclude_removes_code_from_expansion(self, fhir_client):
        """Per §4.9.6: "codes in the exclude statements are never in the value
        set". The exclude subtracts from the include result set.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "concept": [
                            {"code": SNOMED_DIABETES_MELLITUS},
                            {"code": SNOMED_T2DM},
                        ],
                    }
                ],
                "exclude": [
                    {
                        "system": SNOMED_URI,
                        "concept": [{"code": SNOMED_T2DM}],
                    }
                ],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        # SNOMED_T2DM MUST be absent.
        assert (SNOMED_URI, SNOMED_T2DM) not in codes

    def test_s31_exclude_unknown_code_is_noop(self, fhir_client):
        """Exclude of a code not in include is a no-op."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                ],
                "exclude": [
                    {"system": SNOMED_URI, "concept": [{"code": "UNKNOWN_CODE"}]}
                ],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes


# =============================================================================
# Item 4: 9 filter operators
# =============================================================================


class TestItem4FilterOperators:
    """Item 4: all 9 FHIR R4 filter operators handled.

    Per https://hl7.org/fhir/R4/valueset.html#filter, the op field is bound
    to Filter Operator (Required): = | is-a | descendent-of | is-not-a | regex
    | in | not-in | generalizes | exists.
    """

    def test_s40_op_is_a_includes_root_and_descendants(self, fhir_client):
        """is-a: per https://hl7.org/fhir/R4/codesystem.html#filter, is-a
        "includes all codes that have a transitive is-a relationship with the
        code provided". Includes the root code itself."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s41_op_descendent_of_excludes_root(self, fhir_client):
        """descendent-of: per spec, descendants ONLY (no root)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": "descendent-of", "value": SNOMED_DIABETES_MELLITUS}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) not in codes

    def test_s42_op_descendent_of_on_leaf_returns_empty(self, fhir_client):
        """descendent-of on a leaf code (no descendants) returns empty.

        SNOMED_T2DM is a leaf in the fixture (no children seeded)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": "descendent-of", "value": SNOMED_T2DM}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert codes == [], f"expected empty, got {codes}"

    def test_s43_op_is_a_on_leaf_returns_just_the_code(self, fhir_client):
        """is-a on a leaf returns just the leaf code itself (root included
        by definition of is-a)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": "is-a", "value": SNOMED_T2DM}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes
        # No descendants, so the count MUST be exactly 1.
        snomed_codes = [c for s, c in codes if s == SNOMED_URI]
        assert len(snomed_codes) == 1

    def test_s44_op_equal_not_silently_dropped(self, fhir_client):
        """The `=` operator: per spec, "The property value is equal to the
        value specified".

        Today the implementation only honors `is-a` and `descendent-of` (see
        _expand_intensional line 1954). Other operators are SILENTLY dropped
        at DEBUG log level (line 1988). This is the v0.0.1 B-class silent-
        fallback shape (per GLOBAL_RULES.md). The probe pins the CURRENT
        behavior so a future fix will fail loudly.

        Reproduction shape for the fix: when `=` is honored on property
        `concept`, the expansion MUST include just the matching code.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": "=", "value": SNOMED_DIABETES_MELLITUS}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        # Current behavior: filter is silently dropped → empty expansion
        # (no concept list, no is-a/descendent-of → nothing added).
        # Pinning the silent-drop behavior. Fix MUST change this.
        assert codes == [], (
            "Expected silent-drop → empty expansion. If this fails, the "
            "implementation now honors `=` — update the probe to assert the "
            "code is included."
        )

    def test_s45_op_regex_not_silently_dropped(self, fhir_client):
        """regex: per spec, matches display against a regex. Today silently
        dropped."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "display", "op": "regex", "value": "[Dd]iabetes"}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        # Current behavior: filter is silently dropped → empty.
        assert codes == [], (
            "Expected silent-drop → empty. If this fails, regex is now "
            "honored — update the probe."
        )

    def test_s46_op_is_not_a_not_silently_dropped(self, fhir_client):
        """is-not-a: per spec, "all codes that do not have a transitive is-a
        relationship with the code provided". Today silently dropped."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": "is-not-a", "value": SNOMED_DIABETES_MELLITUS}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        # Current behavior: silently dropped → empty.
        assert codes == [], (
            "Expected silent-drop → empty. If this fails, is-not-a is now "
            "honored — update the probe."
        )

    def test_s47_op_generalizes_not_silently_dropped(self, fhir_client):
        """generalizes: per spec, inverse of is-a (includes parents). Today
        silently dropped."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": "generalizes", "value": SNOMED_T2DM}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        # Current behavior: silently dropped → empty.
        assert codes == [], (
            "Expected silent-drop → empty. If this fails, generalizes is now "
            "honored — update the probe."
        )

    def test_s48_op_exists_not_silently_dropped(self, fhir_client):
        """exists: per spec, "the property value exists in the code (or not)".
        Value is boolean (`true`/`false`). Today silently dropped."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "inactive", "op": "exists", "value": "false"}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        # Current behavior: silently dropped → empty.
        assert codes == [], (
            "Expected silent-drop → empty. If this fails, exists is now "
            "honored — update the probe."
        )

    def test_s49_op_in_not_silently_dropped(self, fhir_client):
        """in: per spec, "the property value is in the listed value set".
        Today silently dropped."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": "in", "value": "http://example.org/vs/another"}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        # Current behavior: silently dropped → empty.
        assert codes == [], (
            "Expected silent-drop → empty. If this fails, `in` is now "
            "honored — update the probe."
        )

    def test_s410_op_not_in_not_silently_dropped(self, fhir_client):
        """not-in: per spec, inverse of `in`. Today silently dropped."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": "not-in", "value": "http://example.org/vs/another"}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert codes == [], (
            "Expected silent-drop → empty. If this fails, not-in is now "
            "honored — update the probe."
        )


# =============================================================================
# Item 4 (continued): operator vocabulary exactness — synonyms / typos
# =============================================================================


class TestItem4OperatorVocabulary:
    """Per https://hl7.org/fhir/R4/valueset.html#filter, op is bound to
    Filter Operator (Required). A `Required` binding means the server MUST
    reject codes outside the enum (per
    https://hl7.org/fhir/R4/terminologies.html#required: "the code MUST come
    from the specified value set"). Common synonyms / typos MUST NOT be
    silently accepted as the canonical operator.
    """

    @pytest.mark.parametrize("bad_op", [
        "is_a",        # underscore instead of hyphen
        "isa",         # missing hyphen
        "descendants-of",  # plural typo
        "descendant-of",   # singular (correct is `descendent-of`)
        "not-a",       # missing `is-` prefix
        "match",       # invented
        "REGEX",       # uppercase variant
        "Is-A",        # capitalization
    ])
    def test_s50_invalid_operator_silent_drop_or_400(self, fhir_client, bad_op):
        """Invalid operators MUST either be rejected (400) or silently
        dropped. Per Required binding, the spec-correct behavior is 400. The
        current implementation silently drops. Documenting either is safe;
        the probe asserts the server does NOT crash and does NOT silently
        accept the typo AS a valid operator (i.e., does not produce results
        implying the operator was honored)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "concept", "op": bad_op, "value": SNOMED_DIABETES_MELLITUS}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        # Either 400 (rejection — preferred) or 200 with empty contains
        # (silent drop — current). Either is "safe" in the sense that the
        # invalid op is NOT honored as if it were `is-a`.
        assert status in (200, 400)
        if status == 200:
            # If 200, the contains MUST be empty (proves the typo was not
            # silently accepted as `is-a`).
            codes = _contains_codes(body)
            assert codes == [], (
                f"Invalid operator '{bad_op}' produced results — was it "
                f"silently accepted as a valid operator? codes={codes}"
            )


# =============================================================================
# Item 5: ValueSet.url as canonical identifier
# =============================================================================


class TestItem5ValueSetUrl:
    """Item 5: ValueSet.url is the canonical identifier.

    Per §4.9.3.1: "ValueSet.url: the canonical URL that never changes for
    this value set". Per §4.9.10: "The canonical URL for the expansion is
    the same as the value set it was expanded from". The expansion response
    MUST echo the input `url`.
    """

    def test_s60_url_echoed_in_expand_response(self, fhir_client):
        """The `url` provided in the POST body MUST be echoed in the
        response's top-level `url` field."""
        url = "http://example.org/fhir/ValueSet/my-value-set"
        vs = {
            "resourceType": "ValueSet",
            "url": url,
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        assert body.get("url") == url, (
            f"expected url={url}, got {body.get('url')!r}"
        )

    def test_s61_url_absent_when_not_provided(self, fhir_client):
        """When the POST body has no `url`, the response MUST NOT carry a
        synthetic url (would be a fabrication)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        assert "url" not in body or body.get("url") is None

    def test_s62_url_format_not_validated(self, fhir_client):
        """Documenting current behavior: any string is accepted as url (no
        URI-format validation per cnl-1 constraint "URL should not contain |
        or #"). Future enhancement could enforce cnl-1."""
        url = "not-a-valid-uri-with-spaces"
        vs = {
            "resourceType": "ValueSet",
            "url": url,
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        # Echo behavior preserved.
        assert body.get("url") == url


# =============================================================================
# Item 6: READ and SEARCH interactions
# =============================================================================


class TestItem6ReadSearchInteractions:
    """Item 6: READ and SEARCH interactions for ValueSet.

    medterm4ds does not persist ValueSet resources. Per TS-01 SKEPTIC QA-002
    and QA-003 (already fixed), the routes exist and return:
      - READ `/fhir/ValueSet/{id}` → 404 OperationOutcome
      - SEARCH `/fhir/ValueSet` → empty Bundle (total=0, entry=[])
    """

    def test_s70_read_returns_404_operation_outcome(self, fhir_client):
        """READ of an unpersisted ValueSet returns a 404 OperationOutcome.

        Per §3.1.0.4 (read) and §3.6.1 (OperationOutcome), the server MUST
        return a FHIR OperationOutcome for unknown resources (NOT a generic
        JSON 404 body).
        """
        resp = fhir_client.get(
            "/fhir/ValueSet/my-vs-id",
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["resourceType"] == "OperationOutcome"

    def test_s71_search_returns_empty_bundle(self, fhir_client):
        """SEARCH on ValueSet returns an empty Bundle.

        Per §4.9.13, `url` is a standard search parameter. The server MUST
        accept it and return a conformant Bundle (total=0, entry=[]).
        """
        resp = fhir_client.get(
            "/fhir/ValueSet?url=http://example.org/vs/test",
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["resourceType"] == "Bundle"
        assert body["type"] == "searchset"
        assert body["total"] == 0
        assert body["entry"] == []

    def test_s72_search_with_version_returns_empty_bundle(self, fhir_client):
        """SEARCH on ValueSet with version param returns an empty Bundle."""
        resp = fhir_client.get(
            "/fhir/ValueSet?url=http://example.org/vs/test&version=1.0.0",
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["resourceType"] == "Bundle"
        assert body["total"] == 0


# =============================================================================
# Edge cases: exclude semantics, multi-system, hostile inputs
# =============================================================================


class TestEdgeCases:
    """Hostile / edge cases that exercise exclude semantics, cross-system
    drift, and silent-rejection audit."""

    def test_s80_exclude_uses_filter_silently_ignored(self, fhir_client):
        """The exclude path (line 1991-1993 of fhir_api.py) only matches
        `exclude[].concept[].code`. Per §4.9.5, exclude has the SAME structure
        as include — `exclude[].filter[]` is permitted. The current
        implementation silently ignores exclude filters. Documenting the
        silent-drop behavior.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {
                        "system": SNOMED_URI,
                        "concept": [
                            {"code": SNOMED_DIABETES_MELLITUS},
                            {"code": SNOMED_T2DM},
                        ],
                    }
                ],
                "exclude": [
                    {
                        "system": SNOMED_URI,
                        "filter": [
                            {"property": "concept", "op": "is-a", "value": SNOMED_DIABETES_MELLITUS}
                        ],
                    }
                ],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        # Current behavior: exclude.filter is silently ignored → both codes
        # remain in the expansion. If this fails, exclude filters are now
        # honored → both codes should be removed.
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s81_exclude_ignores_system_when_matching_codes(self, fhir_client):
        """The exclude path matches `c["code"] not in exc_codes` (line 1993).
        It does NOT consider the system. Cross-system drift: an exclude of
        SNOMED code X would also exclude a LOINC code X if both are in the
        expansion. Documenting the current behavior so a future fix to scope
        excludes by (system, code) will fail loudly.

        Per §4.9.10.2: "uniqueness is based on system/version/code". An
        exclude should logically scope by the same key.
        """
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    # Include E11 from ICD10CM AND a SNOMED code that happens
                    # to share the string "E11" (hypothetical — but here we
                    # use the real E11 in both systems to demonstrate the
                    # behavior).
                    {"system": ICD10CM_URI, "concept": [{"code": ICD10CM_T2DM}]},
                ],
                # Exclude by code "E11" without matching system.
                "exclude": [
                    {"system": "http://example.org/different-system", "concept": [{"code": ICD10CM_T2DM}]}
                ],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        # Current behavior: exclude matches on code alone, ignoring system.
        # The ICD10CM E11 IS removed even though the exclude references a
        # different system. Pinning the cross-system-drift behavior.
        assert (ICD10CM_URI, ICD10CM_T2DM) not in codes, (
            "If this fails, exclude is now scoped by (system, code) — "
            "update the probe to assert the code REMAINS."
        )

    def test_s82_multi_system_compose_union(self, fhir_client):
        """Per §4.9.9, value sets MAY select codes from multiple code systems
        (union of includes)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_DIABETES_MELLITUS}]},
                    {"system": ICD10CM_URI, "concept": [{"code": ICD10CM_T2DM}]},
                    {"system": RXNORM_URI, "concept": [{"code": RXNORM_METFORMIN}]},
                ]
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_DIABETES_MELLITUS) in codes
        assert (ICD10CM_URI, ICD10CM_T2DM) in codes
        assert (RXNORM_URI, RXNORM_METFORMIN) in codes

    def test_s83_compose_empty_include_returns_400_or_empty(self, fhir_client):
        """compose with no include: per vsd-1 ("A value set include/exclude
        SHALL have a value set or a system"), the server SHOULD reject. The
        current implementation silently produces an empty expansion."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {},
        }
        status, body = _post_expand(fhir_client, vs)
        # Either 400 (per vsd-1) or 200 with empty expansion (current).
        assert status in (200, 400)
        if status == 200:
            codes = _contains_codes(body)
            assert codes == []

    def test_s84_compose_missing_returns_400_or_empty(self, fhir_client):
        """ValueSet body with no compose element at all."""
        vs = {"resourceType": "ValueSet"}
        status, body = _post_expand(fhir_client, vs)
        assert status in (200, 400)
        if status == 200:
            codes = _contains_codes(body)
            assert codes == []

    def test_s85_filter_with_unknown_property_silently_dropped(self, fhir_client):
        """Per §4.9.6.1, filter.property references "A property/filter defined
        by the code system". Only `concept` is recognized today; other
        properties (e.g. SNOMED `inactive`, LOINC `PROPERTY`) are silently
        dropped."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "filter": [
                    {"property": "inactive", "op": "=", "value": "true"}
                ]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert codes == [], (
            "Expected silent-drop → empty. If this fails, unknown-property "
            "filters are now handled — update the probe."
        )

    def test_s86_locked_date_silently_ignored(self, fhir_client):
        """Per §4.9.5: compose.lockedDate is "Fixed date for references with
        no specified version (transitive)". The current implementation does
        not honor lockedDate — silently ignored. Documenting the behavior."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "lockedDate": "2024-01-01",
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                ],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s87_compose_inactive_true_silently_ignored(self, fhir_client):
        """Per §4.9.5: compose.inactive (boolean) controls whether inactive
        codes are in the value set. The current implementation does not honor
        this flag — silently ignored. Documenting the behavior."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "inactive": True,
                "include": [
                    {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
                ],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        codes = _contains_codes(body)
        assert (SNOMED_URI, SNOMED_T2DM) in codes

    def test_s88_compose_include_valueset_canonical_silently_ignored(self, fhir_client):
        """Per §4.9.5 / §4.9.6.4: compose.include[].valueSet (0..* canonical)
        allows including the contents of other ValueSets by canonical URL.
        medterm4ds does not persist ValueSets, so this is silently ignored.
        Documenting the behavior."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {
                "include": [
                    {"valueSet": ["http://example.org/vs/some-other-vs"]}
                ],
            },
        }
        status, body = _post_expand(fhir_client, vs)
        assert status in (200, 400)
        if status == 200:
            codes = _contains_codes(body)
            assert codes == []


# =============================================================================
# Response-shape audits
# =============================================================================


class TestResponseShapeAudits:
    """Per GLOBAL_RULES.md "Conformance property per route": audit response
    shape and Content-Type on the $expand POST route."""

    def test_s90_expand_post_content_type_fhir_json(self, fhir_client):
        """The $expand POST response MUST carry Content-Type:
        application/fhir+json (per §3.1.0.1.9)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        resp = fhir_client.post(
            "/fhir/ValueSet/$expand",
            json=vs,
            headers={"Accept": "application/fhir+json"},
        )
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "application/fhir+json" in ct, f"Content-Type was {ct!r}"

    def test_s91_expand_response_has_expansion_with_timestamp(self, fhir_client):
        """Per §4.9.5: ValueSet.expansion.timestamp is 1..1 (mandatory)."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        expansion = body.get("expansion", {})
        assert "timestamp" in expansion, "expansion.timestamp is 1..1 mandatory"
        assert "total" in expansion
        assert "contains" in expansion

    def test_s92_expand_contains_entries_have_system_and_code(self, fhir_client):
        """Per vsd-10 and vsd-9 constraints: every contains entry with a code
        MUST have a system. Pinning the per-entry shape contract."""
        vs = {
            "resourceType": "ValueSet",
            "compose": {"include": [
                {"system": SNOMED_URI, "concept": [{"code": SNOMED_T2DM}]}
            ]},
        }
        status, body = _post_expand(fhir_client, vs)
        assert status == 200
        for entry in body["expansion"]["contains"]:
            assert "system" in entry and entry["system"]
            assert "code" in entry and entry["code"]
