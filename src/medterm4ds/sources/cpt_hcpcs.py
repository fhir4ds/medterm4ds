"""CPT and HCPCS hierarchy/display rules."""

from __future__ import annotations

from .base import DefaultStrategy

# CPT cross-reference target priority order
CPT_TARGET_PRIORITY: dict[str, int] = {
    "HCPCS": 0,
    "ICD10CM": 1,
    "SNOMEDCT_US": 2,
}


class CptStrategy(DefaultStrategy):
    """CPT source strategy with isa hierarchy and guarded SNOMED fallback."""

    def __init__(self) -> None:
        super().__init__(source="CPT")

    def hierarchy_edge_sql(self) -> str | None:
        """CPT uses RELA='isa' for hierarchy."""
        return "r.RELA = 'isa'"

    def friendly_strategy_rows(self) -> list[dict[str, object]]:
        """CPT: native hierarchy -> guarded SNOMED fallback -> original."""
        return [
            # Native hierarchy: MEDLINEPLUS
            {
                "phase": "native",
                "walk_kind": "parent",
                "target_source": "MEDLINEPLUS",
                "target_tty": None,
                "match_type": "exact",
                "priority": 0,
                "max_depth": 5,
                "stop_on_hit": True,
                "guard": None,
            },
            # Native hierarchy: CHV
            {
                "phase": "native",
                "walk_kind": "parent",
                "target_source": "CHV",
                "target_tty": None,
                "match_type": "exact",
                "priority": 1,
                "max_depth": 5,
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
                "priority": 2,
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
                "priority": 3,
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
        """CPT: shorter display preferred, with TTY priority."""
        return (
            "CASE upper(TTY) "
            "WHEN 'ETCF' THEN 0 "
            "WHEN 'ETCLIN' THEN 1 "
            "WHEN 'PT' THEN 2 "
            "WHEN 'SY' THEN 3 "
            "ELSE 4 END, "
            "CASE upper(TTY) WHEN 'SY' THEN LENGTH(STR) ELSE 0 END, "
            "LENGTH(STR), "
            "AUI"
        )


class HcpcsStrategy(DefaultStrategy):
    """HCPCS source strategy with PAR hierarchy."""

    def __init__(self) -> None:
        super().__init__(source="HCPCS")

    def hierarchy_edge_sql(self) -> str | None:
        """HCPCS uses REL='PAR' AND RELA IS NULL."""
        return "r.REL = 'PAR' AND r.RELA IS NULL"

    def friendly_strategy_rows(self) -> list[dict[str, object]]:
        """HCPCS: native hierarchy -> SNOMED fallback -> original.

        Composed via DefaultStrategy helpers — row schema lives in one place.
        """
        return (
            self._native_rows(["MEDLINEPLUS", "CHV"]) +
            self._snomed_fallback_rows(["MEDLINEPLUS", "CHV"], start_priority=2) +
            [self._original_row()]
        )

    def atom_display_rank(self) -> str:
        return "AUI"
