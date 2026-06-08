"""LOINC component/common-name rules and blacklisted terms."""

from __future__ import annotations

from .base import DefaultStrategy

# Generic LOINC terms that should not be used as patient-friendly names
BLACKLIST_LOINC: frozenset[str] = frozenset({
    "I",
    "A",
    "IgE",
    "IgG",
    "Specimen",
    "Activity",
    "Multisection",
    "Nuclear",
    "E",
    "G Ab",
})


class LoincStrategy(DefaultStrategy):
    """LOINC source strategy with component/axis/common-name tiers."""

    def __init__(self) -> None:
        super().__init__(source="LNC")

    def hierarchy_edge_sql(self) -> str | None:
        """LOINC uses REL='PAR' AND RELA IS NULL with CHD reversal."""
        return "r.REL = 'PAR' AND r.RELA IS NULL"

    def friendly_strategy_rows(self) -> list[dict[str, object]]:
        """LOINC: component/axis/common-name -> native hierarchy -> SNOMED fallback."""
        return [
            # Tier 1: first-axis component part
            {
                "phase": "axis",
                "walk_kind": "component_of",
                "target_source": "LNC",
                "target_tty": "LPDN",
                "match_type": "first_axis",
                "priority": 0,
                "max_depth": 1,
                "stop_on_hit": True,
                "guard": "blacklist_loinc",
            },
            # Tier 2: component CUI cross-reference to MEDLINEPLUS/CHV
            {
                "phase": "component_cui",
                "walk_kind": "component_of",
                "target_source": "MEDLINEPLUS",
                "target_tty": None,
                "match_type": "component",
                "priority": 1,
                "max_depth": 1,
                "stop_on_hit": True,
                "guard": None,
            },
            {
                "phase": "component_cui",
                "walk_kind": "component_of",
                "target_source": "CHV",
                "target_tty": None,
                "match_type": "component",
                "priority": 2,
                "max_depth": 1,
                "stop_on_hit": True,
                "guard": None,
            },
            # Tier 3: native hierarchy walk with MEDLINEPLUS/CHV
            {
                "phase": "native",
                "walk_kind": "parent",
                "target_source": "MEDLINEPLUS",
                "target_tty": None,
                "match_type": "broader",
                "priority": 3,
                "max_depth": 5,
                "stop_on_hit": True,
                "guard": None,
            },
            {
                "phase": "native",
                "walk_kind": "parent",
                "target_source": "CHV",
                "target_tty": None,
                "match_type": "broader",
                "priority": 4,
                "max_depth": 5,
                "stop_on_hit": True,
                "guard": None,
            },
            # Tier 4: LOINC common name
            {
                "phase": "common_name",
                "walk_kind": "same_cui",
                "target_source": "LNC",
                "target_tty": "LC",
                "match_type": "loinc_common",
                "priority": 5,
                "max_depth": 0,
                "stop_on_hit": True,
                "guard": None,
            },
            # SNOMED fallback
            {
                "phase": "fallback",
                "walk_kind": "snomed",
                "target_source": "MEDLINEPLUS",
                "target_tty": None,
                "match_type": "broader",
                "priority": 6,
                "max_depth": 5,
                "stop_on_hit": True,
                "guard": "snomed_top_level",
            },
            {
                "phase": "fallback",
                "walk_kind": "snomed",
                "target_source": "CHV",
                "target_tty": None,
                "match_type": "broader",
                "priority": 7,
                "max_depth": 5,
                "stop_on_hit": True,
                "guard": "snomed_top_level",
            },
            # Original display
            {
                "phase": "original",
                "walk_kind": "none",
                "target_source": None,
                "target_tty": None,
                "match_type": "original",
                "priority": 99,
                "max_depth": 0,
                "stop_on_hit": True,
                "guard": None,
            },
        ]

    def atom_display_rank(self) -> str:
        """LOINC uses basic AUI ordering (no special TTY ranking)."""
        return "AUI"
