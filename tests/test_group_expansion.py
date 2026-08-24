"""Tests for the composite-group dictionary pass (decomposition P3).

Stubs the canonical value sets so no artifact files are needed: the
dictionary build, exact-match fan-out, fall-through, normalization,
filters, and count semantics are all exercised against constructed data.
"""

from __future__ import annotations

import pytest

from medterm4ds.services.search import CanonicalSearchResult, SearchService

# Stub value sets shaped like the deployed artifacts: a panel parent with
# composite_groups, three component anchors, and an ungrouped control.
_VSETS = [
    {
        "canonical_id": "VAL-LAB-LOINC-24331-1",
        "system": "LOINC",
        "code": "24331-1",
        "patient_friendly_name": "Lipid panel",
        "consumer_synonyms": ["lipid profile"],
        "domain": ["Laboratory"],
        "members": [],
        "composite_groups": [
            {"group_id": "GP-24331-1", "term": "Lipid 1996 panel", "kind": "composite_group"},
        ],
    },
    {
        "canonical_id": "VAL-LAB-LOINC-2085-9",
        "system": "LOINC", "code": "2085-9",
        "patient_friendly_name": "Cholesterol in HDL",
        "consumer_synonyms": [], "domain": ["Laboratory"], "members": [],
        "composite_groups": [{"group_id": "GP-24331-1", "term": "", "kind": "composite_group"}],
    },
    {
        "canonical_id": "VAL-LAB-LOINC-2571-8",
        "system": "LOINC", "code": "2571-8",
        "patient_friendly_name": "Triglycerides",
        "consumer_synonyms": [], "domain": ["Laboratory"], "members": [],
        "composite_groups": [{"group_id": "GP-24331-1", "term": "", "kind": "composite_group"}],
    },
    {
        "canonical_id": "VAL-COND-SNOMED-73211009",
        "system": "SNOMEDCT_US", "code": "73211009",
        "patient_friendly_name": "Diabetes mellitus",
        "consumer_synonyms": [], "domain": ["Condition"], "members": [],
    },
]


@pytest.fixture()
def svc(monkeypatch):
    """SearchService with canonical state loaded from the stub vsets."""
    service = SearchService()
    # Bypass file loading: replicate _ensure_canonical's state setup.
    for v in _VSETS:
        cid = v["canonical_id"]
        service._canonical_by_id[cid] = {
            **v, "anchor_system": v["system"], "anchor_code": v["code"],
        }
        service._canonical_by_anchor[(v["system"], v["code"])] = cid
    service._build_group_dictionary(_VSETS)
    service._canonical_loaded = True
    return service


class TestGroupDictionaryBuild:
    def test_group_terms_built_from_composite_groups(self, svc):
        # Materialized term + parent name + parent synonyms all indexed
        assert "lipid 1996 panel" in svc._group_terms
        assert "lipid panel" in svc._group_terms
        assert "lipid profile" in svc._group_terms

    def test_terms_whitespace_collapsed(self, svc):
        svc2 = svc
        vsets2 = [{**_VSETS[0], "composite_groups": [
            {"group_id": "GP-X", "term": "  Basic   metabolic  panel ", "kind": "composite_group"},
        ]}]
        svc2._build_group_dictionary(vsets2)
        assert "basic metabolic panel" in svc2._group_terms


class TestGroupDictionarySearch:
    def test_exact_term_fans_out_parent_first(self, svc):
        rows = svc._group_dictionary_search(
            "lipid panel", result_types=None, sources=None, count=10)
        assert rows and rows[0].canonical_id == "VAL-LAB-LOINC-24331-1"
        assert {r.canonical_id for r in rows} == {
            "VAL-LAB-LOINC-24331-1", "VAL-LAB-LOINC-2085-9", "VAL-LAB-LOINC-2571-8"}
        assert all(r.group_id == "GP-24331-1" for r in rows)
        assert rows[0].group_cids[0] == "VAL-LAB-LOINC-24331-1"
        assert rows[0].match_grade == "exact"
        assert rows[0].matched_via_code == "group:GP-24331-1"

    def test_miss_returns_empty(self, svc):
        assert svc._group_dictionary_search(
            "diabetes mellitus", result_types=None, sources=None, count=10) == []

    def test_query_normalization_case_and_whitespace(self, svc):
        rows = svc._group_dictionary_search(
            "  LIPID   panel ", result_types=None, sources=None, count=10)
        assert rows and rows[0].group_id == "GP-24331-1"

    def test_source_filter_excludes_foreign_rows(self, svc):
        rows = svc._group_dictionary_search(
            "lipid panel", result_types=None, sources=["CVX"], count=10)
        # Group is all-LOINC; a CVX filter must drop every row
        assert rows == []

    def test_result_type_prefix_filters_components(self, svc):
        rows = svc._group_dictionary_search(
            "lipid panel", result_types="condition", sources=None, count=10)
        assert rows == []  # no VAL-COND member in this group

    def test_count_truncates_but_group_cids_stay_complete(self, svc):
        rows = svc._group_dictionary_search(
            "lipid panel", result_types=None, sources=None, count=2)
        assert len(rows) == 2
        # Every row still carries the FULL group so consumers detect truncation
        assert len(rows[0].group_cids) == 3

    def test_to_dict_carries_group_fields(self, svc):
        rows = svc._group_dictionary_search(
            "lipid panel", result_types=None, sources=None, count=10)
        d = rows[0].to_dict()
        assert d["group_id"] == "GP-24331-1"
        assert len(d["group_cids"]) == 3

    def test_result_type_field_default_none(self):
        # Pre-migration consumers: no group fields means plain single result
        r = CanonicalSearchResult(
            canonical_id="X", domain=[], anchor_system="LOINC", anchor_code="1",
            patient_friendly_name="n", score=1.0, match_grade="exact",
            matched_via_code="c", matched_via_display="d", total_member_count=0)
        assert r.group_id is None and r.group_cids == []
        assert "group_id" not in r.to_dict()
