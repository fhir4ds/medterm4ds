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
        # Composed via DefaultStrategy helpers — row schema lives in one place.
        return (
            self._native_rows(["MEDLINEPLUS", "CHV"]) +
            self._snomed_fallback_rows(["MEDLINEPLUS", "CHV"], start_priority=2) +
            [self._original_row()]
        )

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
