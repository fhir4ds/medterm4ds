"""SourceStrategy protocol, default rules, and shared constants."""

from __future__ import annotations

from typing import Protocol

# ---------------------------------------------------------------------------
# Broad name sets -- used across multiple sources to filter generic headings
# ---------------------------------------------------------------------------

BROAD_CHV_NAMES: frozenset[str] = frozenset({
    "clinical findings",
    "clinical investigation",
    "cpt",
    "hydrolase",
    "hydrolases",
    "operation",
    "operations",
    "sign and symptom",
    "signs and symptoms",
    "symptoms and signs",
    "service",
    "services",
    "finding",
    "findings",
    "symptom",
    "symptoms",
})

BROAD_MEDLINEPLUS_NAMES: frozenset[str] = frozenset({
    "anatomy",
    "body structure",
    "body structures",
    "clinical finding",
    "disease inflammatory",
    "finding",
    "findings",
    "physical finding",
    "procedure",
})


class SourceStrategy(Protocol):
    """Interface that each UMLS source must implement.

    Methods return SQL fragments or strategy row dicts that the DuckDB engine
    uses to drive hierarchy extraction and patient-friendly resolution.
    """

    source: str

    def hierarchy_edge_sql(self) -> str | None:
        """Return SQL WHERE fragment for mrrel to extract hierarchy edges.

        Return None if the source has no standard hierarchy edges.
        """
        ...

    def friendly_strategy_rows(self) -> list[dict[str, object]]:
        """Return patient-friendly strategy rows for this source.

        Each row has: phase, walk_kind, target_source, target_tty,
        match_type, priority, max_depth, stop_on_hit, guard
        """
        ...

    def atom_display_rank(self) -> str:
        """Return SQL fragment for atom display ranking.

        Should return a CASE expression or column reference that ranks atoms.
        """
        ...


class DefaultStrategy:
    """Sensible defaults for sources without specialized logic."""

    def __init__(self, source: str = "") -> None:
        self.source = source

    def hierarchy_edge_sql(self) -> str | None:
        """Default: no hierarchy edges."""
        return None

    def friendly_strategy_rows(self) -> list[dict[str, object]]:
        """Default: generic SNOMED fallback rows."""
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
        """Default: basic suppress-based ranking."""
        return (
            "CASE WHEN SUPPRESS = 'N' THEN 0 ELSE 1 END, "
            "CASE TTY WHEN 'PT' THEN 0 WHEN 'MH' THEN 1 WHEN 'LN' THEN 2 ELSE 3 END, "
            "AUI"
        )
