"""Generic UMLS source rules for sources without specialized strategies."""

from __future__ import annotations

from .base import DefaultStrategy


class GenericStrategy(DefaultStrategy):
    """Rules for sources like ATC, MSH, and other minor vocabularies."""

    def __init__(self, source: str) -> None:
        super().__init__(source=source)

    def hierarchy_edge_sql(self) -> str | None:
        """Generic sources use RELA='isa' for hierarchy."""
        return "r.RELA = 'isa'"

    def friendly_strategy_rows(self) -> list[dict[str, object]]:
        """Generic parent walk with MEDLINEPLUS/CHV then original display."""
        return [
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
        return "AUI"
