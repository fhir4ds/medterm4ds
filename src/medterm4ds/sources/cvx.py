"""CVX vaccine lookup/enrichment rules."""

from __future__ import annotations

from .base import DefaultStrategy

# CDC vaccine group metadata URL
CVX_GROUP_URL: str = (
    "https://www2.cdc.gov/vaccines/iis/iisstandards/downloads/VG.txt"
)


class CvxStrategy(DefaultStrategy):
    """CVX source strategy -- lookup/enrichment only, no hierarchy."""

    def __init__(self) -> None:
        super().__init__(source="CVX")

    def hierarchy_edge_sql(self) -> str | None:
        """CVX has no hierarchy."""
        return None

    def friendly_strategy_rows(self) -> list[dict[str, object]]:
        """CVX: lookup/enrichment via vaccine group mapping.

        Composed via DefaultStrategy helpers — row schema lives in one place.
        """
        return [
            self._strategy_row(
                phase="enrichment",
                walk_kind="cvx_group",
                target_source="CVX",
                match_type="cvx_group",
                priority=0,
                max_depth=0,
            ),
            self._original_row(),
        ]

    def atom_display_rank(self) -> str:
        """CVX: PT/SY/AB ranking with shorter display preferred."""
        return (
            "CASE upper(TTY) "
            "WHEN 'PT' THEN 0 "
            "WHEN 'SY' THEN 1 "
            "WHEN 'AB' THEN 2 "
            "ELSE 3 END, "
            "LENGTH(STR), "
            "AUI"
        )
