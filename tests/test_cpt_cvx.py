"""Tests for the vendored CDC data: CPT↔CVX crosswalk and CVX groups."""

from __future__ import annotations

import pytest

from medterm4ds.core.models import CodeMapping, CodeRef
from medterm4ds.services.cpt_cvx import get_cpt_cvx_mappings
from medterm4ds.services.cvx_group_data import load_cvx_group_rows


class TestCptCvxLoader:
    def test_rows_parse_with_padding_stripped(self):
        rows = get_cpt_cvx_mappings(
            [CodeRef("CPT", "90281")], target_sources=["CVX"]
        )
        assert len(rows) == 1
        m = rows[0]
        # CDC pads codes with spaces ("90281     ", "86        ").
        assert m.source.code == "90281"
        assert m.target.code == "86"
        assert m.target.source == "CVX"
        assert m.match_type == "cdc_cpt_cvx"
        assert m.relationship == "equivalent"
        assert m.source_display == "Immune globulin (Ig), human, for intramuscular use"
        assert m.target_display == "IG"

    def test_unknown_cpt_returns_empty(self):
        assert get_cpt_cvx_mappings(
            [CodeRef("CPT", "99999")], target_sources=["CVX"]
        ) == []

    def test_irrelevant_pair_returns_empty(self):
        # CPT→ICD10CM is not the CDC table's concern — caller falls through
        # to the engine path unchanged.
        assert get_cpt_cvx_mappings(
            [CodeRef("CPT", "90281")], target_sources=["ICD10CM"]
        ) == []
        assert get_cpt_cvx_mappings(
            [CodeRef("ICD10CM", "E11.9")], target_sources=["CVX"]
        ) == []

    def test_reverse_is_one_to_many(self):
        # CVX 34 (RIG) is the best CVX for two rabies-Ig CPT codes.
        rows = get_cpt_cvx_mappings(
            [CodeRef("CVX", "34")], target_sources=["CPT"]
        )
        targets = sorted(m.target.code for m in rows)
        assert targets == ["90375", "90376"]
        assert all(m.match_type == "cdc_cpt_cvx" for m in rows)
        assert all(m.target.source == "CPT" for m in rows)


class TestMappingServiceMerge:
    """get_code_mappings merges CDC rows with engine rows (CDC wins,
    sorts first, budget respected)."""

    @staticmethod
    def _engine_with(rows):
        class StubEngine:
            def get_code_mappings(self, codes, *, target_sources, **kwargs):
                return [
                    CodeMapping(
                        source=r["source"], target=r["target"],
                        relationship="equivalent",
                        match_type=r.get("match_type", "same_cui"),
                    )
                    for r in rows
                ]

        return StubEngine()

    def _call(self, engine, codes, targets):
        from medterm4ds.services.mapping import get_code_mappings

        return get_code_mappings(codes, engine, target_sources=targets)

    def test_cdc_row_replaces_conflicting_engine_row(self, monkeypatch):
        monkeypatch.delenv("MEDTERM4DS_CVX_GROUP_URL", raising=False)
        engine = self._engine_with([
            # Same (source, target) pair as the CDC row — dropped as a
            # duplicate; the CDC row (single-best) represents it.
            {"source": CodeRef("CPT", "90281"),
             "target": CodeRef("CVX", "86"), "match_type": "same_cui"},
            # Different target — an additional engine mapping, kept.
            {"source": CodeRef("CPT", "90281"),
             "target": CodeRef("CVX", "10"), "match_type": "same_cui"},
        ])
        results = self._call(engine, [CodeRef("CPT", "90281")], ["CVX"])
        pairs = [(m.source.code, m.target.code, m.match_type) for m in results]
        assert pairs == [
            ("90281", "86", "cdc_cpt_cvx"),
            ("90281", "10", "same_cui"),
        ]

    def test_non_vaccine_pair_untouched(self):
        engine = self._engine_with([
            {"source": CodeRef("ICD10CM", "E11.9"),
             "target": CodeRef("SNOMEDCT_US", "44054006")},
        ])
        results = self._call(
            engine, [CodeRef("ICD10CM", "E11.9")], ["SNOMEDCT_US"]
        )
        assert len(results) == 1
        assert results[0].match_type == "same_cui"

    def test_budget_keeps_cdc_rows_first(self):
        engine = self._engine_with([
            {"source": CodeRef("CPT", "90281"),
             "target": CodeRef("CVX", str(code)), "match_type": "same_cui"}
            for code in (10, 11, 12)
        ])
        results = self._call(
            engine, [CodeRef("CPT", "90281")], ["CVX"],
        )
        # Default budget is 50, so everything fits; with a tight budget the
        # CDC row must survive.
        from medterm4ds.services.mapping import get_code_mappings

        tight = get_code_mappings(
            [CodeRef("CPT", "90281")], engine, target_sources=["CVX"],
            max_results_per_code=1,
        )
        assert len(results) == 4
        assert [m.target.code for m in tight] == ["86"]
        assert tight[0].match_type == "cdc_cpt_cvx"


class TestCvxGroupData:
    def test_all_five_columns_present(self):
        rows = load_cvx_group_rows()
        assert len(rows) > 200
        for code, short, status, group_name, group_cvx in rows:
            assert code.isdigit() and group_cvx.isdigit()
            assert short and group_name and status

    @pytest.mark.parametrize(
        "code,group_name,group_cvx",
        [
            ("01", "DTAP", "107"),   # DTP groups under unspecified DTaP
            ("165", "HPV", "137"),   # HPV9 groups under unspecified HPV
            ("208", "COVID-19", "213"),
        ],
    )
    def test_known_rows(self, code, group_name, group_cvx):
        matches = [r for r in load_cvx_group_rows() if r[0] == code]
        assert (group_name, group_cvx) in [(r[3], r[4]) for r in matches]
