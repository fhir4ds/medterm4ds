"""SNOMED CT isa/top-level guard rules and target routing."""

from __future__ import annotations

from .base import DefaultStrategy

# Sources that can fall back to SNOMED for patient-friendly resolution
SNOMED_FALLBACK_SOURCES: frozenset[str] = frozenset({
    "ICD10CM",
    "ICD10PCS",
    "LNC",
    "HCPCS",
    "CPT",
})

# When mapping SNOMED to other sources, this is the priority order
SNOMED_TARGET_PRIORITY: dict[str, int] = {
    "ICD10CM": 0,
    "ICD10PCS": 1,
    "LNC": 2,
    "CPT": 3,
    "HCPCS": 4,
}

# Depth threshold: SNOMED codes at this depth or shallower are "top-level"
SNOMED_TOP_LEVEL_GUARD_DEPTH: int = 3

# Match types exempt from the top-level guard
SNOMED_TOP_LEVEL_GUARD_EXEMPT_MATCH_TYPES: frozenset[str] = frozenset({"same_cui"})


class SnomedStrategy(DefaultStrategy):
    """SNOMED CT source strategy with isa hierarchy and top-level guard."""

    def __init__(self) -> None:
        super().__init__(source="SNOMEDCT_US")

    def hierarchy_edge_sql(self) -> str | None:
        """SNOMED uses RELA='isa' with PAR/CHD handling."""
        return "r.REL = 'PAR' AND COALESCE(r.RELA, 'isa') IN ('isa', 'inverse_isa')"

    def friendly_strategy_rows(self) -> list[dict[str, object]]:
        """SNOMED: route to target sources first, then guarded SNOMED fallback."""
        return [
            {
                "phase": "snomed_to_target_native_hierarchy",
                "walk_kind": "mapped_from",
                "target_source": "ICD10CM",
                "target_tty": None,
                "match_type": "same_cui",
                "priority": 0,
                "max_depth": 0,
                "stop_on_hit": False,
                "guard": None,
            },
            {
                "phase": "snomed_to_target_native_hierarchy",
                "walk_kind": "mapped_from",
                "target_source": "ICD10PCS",
                "target_tty": None,
                "match_type": "same_cui",
                "priority": 1,
                "max_depth": 0,
                "stop_on_hit": False,
                "guard": None,
            },
            {
                "phase": "snomed_to_target_native_hierarchy",
                "walk_kind": "mapped_from",
                "target_source": "LNC",
                "target_tty": None,
                "match_type": "same_cui",
                "priority": 2,
                "max_depth": 0,
                "stop_on_hit": False,
                "guard": None,
            },
            {
                "phase": "snomed_to_target_native_hierarchy",
                "walk_kind": "mapped_from",
                "target_source": "CPT",
                "target_tty": None,
                "match_type": "same_cui",
                "priority": 3,
                "max_depth": 0,
                "stop_on_hit": False,
                "guard": None,
            },
            {
                "phase": "snomed_to_target_native_hierarchy",
                "walk_kind": "mapped_from",
                "target_source": "HCPCS",
                "target_tty": None,
                "match_type": "same_cui",
                "priority": 4,
                "max_depth": 0,
                "stop_on_hit": False,
                "guard": None,
            },
            {
                "phase": "direct_snomed_guarded_walk",
                "walk_kind": "isa",
                "target_source": "MEDLINEPLUS",
                "target_tty": None,
                "match_type": "broader",
                "priority": 5,
                "max_depth": 5,
                "stop_on_hit": True,
                "guard": "snomed_top_level",
            },
            {
                "phase": "direct_snomed_guarded_walk",
                "walk_kind": "isa",
                "target_source": "CHV",
                "target_tty": None,
                "match_type": "broader",
                "priority": 6,
                "max_depth": 5,
                "stop_on_hit": True,
                "guard": "snomed_top_level",
            },
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
        """SNOMED uses PT/FN/SY ranking with shorter display preferred."""
        return (
            "CASE upper(TTY) "
            "WHEN 'PT' THEN 0 "
            "WHEN 'SCD' THEN 1 "
            "WHEN 'FN' THEN 2 "
            "WHEN 'SY' THEN 3 "
            "ELSE 4 END, "
            "LENGTH(STR), "
            "AUI"
        )
