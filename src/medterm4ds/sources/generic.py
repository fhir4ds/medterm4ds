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
        """Generic parent walk with MEDLINEPLUS/CHV then original display.

        Composed via DefaultStrategy helpers — row schema lives in one place.
        """
        return (
            self._native_rows(["MEDLINEPLUS", "CHV"]) +
            [self._original_row()]
        )

    def atom_display_rank(self) -> str:
        return "AUI"
