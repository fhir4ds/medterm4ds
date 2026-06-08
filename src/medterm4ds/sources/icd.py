"""ICD10CM/ICD10PCS PAR hierarchy rules."""

from __future__ import annotations

from .base import DefaultStrategy


# Kept for compatibility with older imports. Patient-friendly hierarchy must
# not infer prefix/range edges; ICD hierarchy comes only from normalized UMLS
# relationships.
PREFIX_HIERARCHY_SOURCES: frozenset[str] = frozenset()


class IcdStrategy(DefaultStrategy):
    """ICD10CM/ICD10PCS source strategy with PAR hierarchy."""

    def __init__(self, source: str) -> None:
        super().__init__(source=source)

    def hierarchy_edge_sql(self) -> str | None:
        """ICD sources use REL='PAR' AND RELA IS NULL with CHD reversal."""
        return "r.REL = 'PAR' AND r.RELA IS NULL"

    def friendly_strategy_rows(self) -> list[dict[str, object]]:
        """ICD: native parent walk with MEDLINEPLUS/CHV -> SNOMED fallback -> original."""
        return [
            # Native hierarchy: MEDLINEPLUS first
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
            # Native hierarchy: CHV second
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
        """ICD uses PT/HT/AB/ET ranking with shorter display preferred."""
        return (
            "CASE upper(TTY) "
            "WHEN 'PT' THEN 0 "
            "WHEN 'HT' THEN 1 "
            "WHEN 'AB' THEN 2 "
            "WHEN 'ET' THEN 3 "
            "ELSE 4 END, "
            "LENGTH(STR), "
            "AUI"
        )
