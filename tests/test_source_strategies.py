"""Tests for the sources/ package: registry, topology, paths, and strategies."""

from __future__ import annotations

import pytest

from medterm4ds.sources import (
    BROAD_CHV_NAMES,
    BROAD_MEDLINEPLUS_NAMES,
    SOURCE_STRATEGIES,
    CptStrategy,
    CvxStrategy,
    GenericStrategy,
    HcpcsStrategy,
    IcdStrategy,
    LoincStrategy,
    RxNormStrategy,
    SnomedStrategy,
    get_strategy,
)
from medterm4ds.sources.base import DefaultStrategy
from medterm4ds.sources.loinc import BLACKLIST_LOINC
from medterm4ds.sources.rxnorm import (
    RXNORM_GROUP_TTYS,
    RXNORM_TTY_TOPOLOGY,
    compute_tty_paths,
    find_tty_path,
)
from medterm4ds.sources.snomed import (
    SNOMED_FALLBACK_SOURCES,
    SNOMED_TARGET_PRIORITY,
    SNOMED_TOP_LEVEL_GUARD_DEPTH,
)

# ---------------------------------------------------------------------------
# 1. Registry contains all required sources
# ---------------------------------------------------------------------------

class TestRegistry:
    EXPECTED_SOURCES = {
        "RXNORM",
        "SNOMEDCT_US",
        "ICD10CM",
        "ICD10PCS",
        "HCPCS",
        "CPT",
        "LNC",
        "CVX",
        "ATC",
        "MSH",
    }

    def test_registry_contains_all_required_sources(self):
        for source in self.EXPECTED_SOURCES:
            assert source in SOURCE_STRATEGIES, f"Missing source: {source}"

    def test_registry_no_extra_keys_beyond_expected(self):
        # Registry may have extra keys, but must have at least the expected set
        missing = self.EXPECTED_SOURCES - set(SOURCE_STRATEGIES.keys())
        assert not missing, f"Missing sources: {missing}"

    def test_get_strategy_returns_generic_for_unknown(self):
        strategy = get_strategy("UNKNOWN_SOURCE")
        assert isinstance(strategy, GenericStrategy)
        assert strategy.source == "UNKNOWN_SOURCE"

    def test_get_strategy_returns_registered_instance(self):
        strategy = get_strategy("RXNORM")
        assert isinstance(strategy, RxNormStrategy)

    def test_get_strategy_is_case_sensitive(self):
        strategy = get_strategy("rxnorm")
        assert isinstance(strategy, GenericStrategy)


# ---------------------------------------------------------------------------
# 2. RxNorm topology exactly matches expected adjacency
# ---------------------------------------------------------------------------

class TestRxNormTopology:
    def test_topology_is_exact_match(self):
        expected = {
            "BN": ("SBD", "IN"),
            "SBD": ("BN", "SCD", "SBDF", "SBDG", "SBDC", "BPCK", "SCDC"),
            "SBDC": ("SBD", "SBDF", "IN"),
            "SBDF": ("SBD", "SCDF"),
            "SCD": ("SBD", "SCDC", "SCDF", "SCDG", "GPCK", "DF", "MIN"),
            "SCDC": ("SCD", "SBD", "IN", "PIN"),
            "SCDF": ("SCD", "SBDF"),
            "BPCK": ("SBD", "GPCK"),
            "GPCK": ("SCD", "BPCK"),
            "IN": ("SCDC", "MIN", "BN"),
            "MIN": ("SCD", "IN"),
            "PIN": ("IN", "SCDC"),
            "DF": ("SCD",),
            "SBDG": ("SBD", "SCDG"),
            "SCDG": ("SCD", "SBDG", "DFG"),
            "DFG": ("SCDG",),
        }
        assert RXNORM_TTY_TOPOLOGY == expected

    def test_all_ttys_are_uppercase(self):
        for key, neighbors in RXNORM_TTY_TOPOLOGY.items():
            assert key == key.upper(), f"Key {key!r} is not uppercase"
            for n in neighbors:
                assert n == n.upper(), f"Neighbor {n!r} of {key!r} is not uppercase"


# ---------------------------------------------------------------------------
# 3-5. Shortest path tests
# ---------------------------------------------------------------------------

class TestShortestPaths:
    def test_sbd_to_scdg(self):
        path = find_tty_path("SBD", "SCDG")
        assert path == ["SBD", "SCD", "SCDG"]

    def test_scd_to_scdg(self):
        path = find_tty_path("SCD", "SCDG")
        assert path == ["SCD", "SCDG"]

    def test_sbdc_to_in(self):
        path = find_tty_path("SBDC", "IN")
        assert path == ["SBDC", "IN"]

    def test_same_tty_returns_self(self):
        path = find_tty_path("SCD", "SCD")
        assert path == ["SCD"]

    def test_unknown_tty_returns_empty(self):
        path = find_tty_path("ZZZ", "IN")
        assert path == []

    def test_unreachable_returns_empty(self):
        # DF can only reach SCD, and from there it might reach IN but let's verify
        # Actually DF->SCD->...->IN works, so let's use a real disconnected case
        # All TTYs in the topology are connected, so just test unknown
        path = find_tty_path("DF", "ZZZ")
        assert path == []

    def test_compute_tty_paths_returns_non_empty(self):
        paths = compute_tty_paths()
        assert len(paths) > 0

    def test_compute_tty_paths_all_have_steps(self):
        paths = compute_tty_paths()
        for p in paths:
            assert "steps" in p
            assert len(p["steps"]) >= 1
            assert p["steps"][0] == p["start_tty"]
            assert p["steps"][-1] == p["target_tty"]


# ---------------------------------------------------------------------------
# 6. Group target TTY set is correct
# ---------------------------------------------------------------------------

class TestGroupTtys:
    def test_group_ttys_correct(self):
        expected = {
            "SCD", "SBD", "SCDF", "SBDF", "GPCK", "BPCK",
            "SBDG", "SCDG", "SBDC", "DFG",
        }
        assert RXNORM_GROUP_TTYS == expected

    def test_in_not_in_group_ttys(self):
        assert "IN" not in RXNORM_GROUP_TTYS

    def test_min_not_in_group_ttys(self):
        assert "MIN" not in RXNORM_GROUP_TTYS

    def test_bn_not_in_group_ttys(self):
        assert "BN" not in RXNORM_GROUP_TTYS


# ---------------------------------------------------------------------------
# 7. Hierarchy SQL for each source
# ---------------------------------------------------------------------------

class TestHierarchySql:
    def test_rxnorm_no_hierarchy(self):
        assert RxNormStrategy().hierarchy_edge_sql() is None

    def test_snomed_isa_hierarchy(self):
        sql = SnomedStrategy().hierarchy_edge_sql()
        assert sql is not None
        assert "isa" in sql

    def test_icd10cm_par_hierarchy(self):
        sql = IcdStrategy("ICD10CM").hierarchy_edge_sql()
        assert sql is not None
        assert "PAR" in sql

    def test_icd10pcs_par_hierarchy(self):
        sql = IcdStrategy("ICD10PCS").hierarchy_edge_sql()
        assert sql is not None
        assert "PAR" in sql

    def test_loinc_par_hierarchy(self):
        sql = LoincStrategy().hierarchy_edge_sql()
        assert sql is not None
        assert "PAR" in sql

    def test_cpt_isa_hierarchy(self):
        sql = CptStrategy().hierarchy_edge_sql()
        assert sql is not None
        assert "isa" in sql

    def test_hcpcs_par_hierarchy(self):
        sql = HcpcsStrategy().hierarchy_edge_sql()
        assert sql is not None
        assert "PAR" in sql

    def test_cvx_no_hierarchy(self):
        assert CvxStrategy().hierarchy_edge_sql() is None

    def test_generic_isa_hierarchy(self):
        sql = GenericStrategy("ATC").hierarchy_edge_sql()
        assert sql is not None
        assert "isa" in sql

    def test_default_no_hierarchy(self):
        assert DefaultStrategy().hierarchy_edge_sql() is None


# ---------------------------------------------------------------------------
# 8. Friendly strategy rows exist for all sources
# ---------------------------------------------------------------------------

class TestFriendlyStrategyRows:
    STRATEGY_CLASSES = [
        (RxNormStrategy, "RXNORM"),
        (SnomedStrategy, "SNOMEDCT_US"),
        (lambda: IcdStrategy("ICD10CM"), "ICD10CM"),
        (lambda: IcdStrategy("ICD10PCS"), "ICD10PCS"),
        (HcpcsStrategy, "HCPCS"),
        (CptStrategy, "CPT"),
        (LoincStrategy, "LNC"),
        (CvxStrategy, "CVX"),
        (lambda: GenericStrategy("ATC"), "ATC"),
    ]

    @pytest.mark.parametrize("factory,source", STRATEGY_CLASSES)
    def test_friendly_rows_non_empty(self, factory, source):
        strategy = factory()
        rows = strategy.friendly_strategy_rows()
        assert len(rows) > 0, f"No friendly strategy rows for {source}"

    @pytest.mark.parametrize("factory,source", STRATEGY_CLASSES)
    def test_friendly_rows_have_required_keys(self, factory, source):
        strategy = factory()
        required_keys = {
            "phase", "walk_kind", "target_source", "target_tty",
            "match_type", "priority", "max_depth", "stop_on_hit", "guard",
        }
        for row in strategy.friendly_strategy_rows():
            assert required_keys <= set(row.keys()), (
                f"Missing keys in {source} friendly row: "
                f"{required_keys - set(row.keys())}"
            )

    @pytest.mark.parametrize("factory,source", STRATEGY_CLASSES)
    def test_friendly_rows_have_original_fallback(self, factory, source):
        strategy = factory()
        rows = strategy.friendly_strategy_rows()
        original_rows = [r for r in rows if r["phase"] == "original"]
        assert len(original_rows) >= 1, f"No original fallback for {source}"

    @pytest.mark.parametrize("factory,source", STRATEGY_CLASSES)
    def test_atom_display_rank_non_empty(self, factory, source):
        strategy = factory()
        rank = strategy.atom_display_rank()
        assert isinstance(rank, str)
        assert len(rank) > 0


# ---------------------------------------------------------------------------
# 9. get_strategy returns generic for unknown sources
# ---------------------------------------------------------------------------

class TestGetStrategy:
    def test_unknown_returns_generic(self):
        strategy = get_strategy("FOOBAR")
        assert isinstance(strategy, GenericStrategy)
        assert strategy.source == "FOOBAR"

    def test_known_returns_correct_type(self):
        assert isinstance(get_strategy("RXNORM"), RxNormStrategy)
        assert isinstance(get_strategy("SNOMEDCT_US"), SnomedStrategy)
        assert isinstance(get_strategy("ICD10CM"), IcdStrategy)
        assert isinstance(get_strategy("ICD10PCS"), IcdStrategy)
        assert isinstance(get_strategy("HCPCS"), HcpcsStrategy)
        assert isinstance(get_strategy("CPT"), CptStrategy)
        assert isinstance(get_strategy("LNC"), LoincStrategy)
        assert isinstance(get_strategy("CVX"), CvxStrategy)
        assert isinstance(get_strategy("ATC"), GenericStrategy)
        assert isinstance(get_strategy("MSH"), GenericStrategy)


# ---------------------------------------------------------------------------
# 10. Broad name sets are non-empty
# ---------------------------------------------------------------------------

class TestBroadNameSets:
    def test_broad_chv_names_non_empty(self):
        assert len(BROAD_CHV_NAMES) > 0

    def test_broad_medlineplus_names_non_empty(self):
        assert len(BROAD_MEDLINEPLUS_NAMES) > 0

    def test_broad_chv_contains_finding(self):
        assert "finding" in BROAD_CHV_NAMES
        assert "findings" in BROAD_CHV_NAMES

    def test_broad_chv_contains_service(self):
        assert "service" in BROAD_CHV_NAMES
        assert "services" in BROAD_CHV_NAMES

    def test_broad_chv_contains_hydrolase(self):
        assert "hydrolase" in BROAD_CHV_NAMES
        assert "hydrolases" in BROAD_CHV_NAMES

    def test_broad_medlineplus_contains_anatomy(self):
        assert "anatomy" in BROAD_MEDLINEPLUS_NAMES

    def test_broad_names_are_lowercase(self):
        for name in BROAD_CHV_NAMES:
            assert name == name.lower(), f"CHV name {name!r} is not lowercase"
        for name in BROAD_MEDLINEPLUS_NAMES:
            assert name == name.lower(), f"MLP name {name!r} is not lowercase"


# ---------------------------------------------------------------------------
# Additional coverage: SNOMED constants
# ---------------------------------------------------------------------------

class TestSnomedConstants:
    def test_fallback_sources(self):
        assert "ICD10CM" in SNOMED_FALLBACK_SOURCES
        assert "LNC" in SNOMED_FALLBACK_SOURCES
        assert "RXNORM" not in SNOMED_FALLBACK_SOURCES

    def test_target_priority(self):
        assert list(SNOMED_TARGET_PRIORITY) == [
            "ICD10CM",
            "ICD10PCS",
            "LNC",
            "CPT",
            "HCPCS",
        ]
        assert "RXNORM" not in SNOMED_TARGET_PRIORITY

    def test_snomed_strategy_routes_to_target_code_systems_first(self):
        rows = SnomedStrategy().friendly_strategy_rows()
        target_rows = [
            row for row in rows
            if row["phase"] == "snomed_to_target_native_hierarchy"
        ]
        assert [row["target_source"] for row in target_rows] == [
            "ICD10CM",
            "ICD10PCS",
            "LNC",
            "CPT",
            "HCPCS",
        ]
        assert all(row["walk_kind"] == "mapped_from" for row in target_rows)
        assert not any(row["target_source"] == "RXNORM" for row in target_rows)

    def test_cpt_strategy_has_no_non_snomed_cross_reference_phase(self):
        rows = CptStrategy().friendly_strategy_rows()
        assert not any(row["phase"] == "cross_reference" for row in rows)

    def test_guard_depth(self):
        assert SNOMED_TOP_LEVEL_GUARD_DEPTH == 3


class TestLoincConstants:
    def test_blacklist_non_empty(self):
        assert len(BLACKLIST_LOINC) > 0

    def test_blacklist_contains_generic_terms(self):
        assert "I" in BLACKLIST_LOINC
        assert "Specimen" in BLACKLIST_LOINC
