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


# ---------------------------------------------------------------------------
# Hierarchy edge RELA vocabulary (canonical — imported by BOTH the prepared
# builder (engines/duckdb/prepared.py) and the raw-path join builder
# (engines/duckdb/_engine_base.py). Do not redefine locally.
#
# mrrel REL is authoritative for direction, never RELA (found by QC-340/345/349,
# EC-15): UMLS mirrors every hierarchy edge twice —
#   - REL='PAR' or 'RB' rows store the CHILD in AUI1 and the PARENT in AUI2
#     (RELA='inverse_isa', and 'has_member' for ATC class->drug links);
#   - REL='CHD' or 'RN' rows store the PARENT in AUI1 and the CHILD in AUI2
#     (RELA='isa', and 'member_of' for the ATC mirror).
# LOINC's multiaxial hierarchy is NOT mirrored: REL='RO' RELA='class_of' rows
# store the code (child) in AUI1 and the multiaxial class (parent) in AUI2.
# ---------------------------------------------------------------------------

RELA_HIERARCHY_PARENT_SIDE: tuple[str, ...] = (
    "isa",
    "inverse_isa",
    "has_member",  # ATC class has_member drug (REL='PAR': AUI1=drug/child)
)
RELA_HIERARCHY_CHILD_SIDE: tuple[str, ...] = (
    "isa",
    "inverse_isa",
    "member_of",  # ATC drug member_of class (REL='CHD': AUI1=class/parent)
)
LOINC_CLASS_RELA = "class_of"


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

    # ------------------------------------------------------------------
    # Strategy-row composition helpers.
    #
    # Subclasses previously re-emitted the MEDLINEPLUS+CHV native rows
    # verbatim, then added source-specific fallbacks. If the row schema
    # changed (new key, renamed 'guard'), every subclass needed editing.
    # These helpers centralize the schema; subclasses compose with
    # _native_rows / _snomed_fallback_rows / _component_rows / _original_row.
    # ------------------------------------------------------------------

    @staticmethod
    def _strategy_row(
        *,
        phase: str,
        walk_kind: str,
        target_source: str | None,
        match_type: str,
        priority: int,
        max_depth: int = 5,
        stop_on_hit: bool = True,
        guard: str | None = None,
        target_tty: str | None = None,
    ) -> dict[str, object]:
        return {
            "phase": phase,
            "walk_kind": walk_kind,
            "target_source": target_source,
            "target_tty": target_tty,
            "match_type": match_type,
            "priority": priority,
            "max_depth": max_depth,
            "stop_on_hit": stop_on_hit,
            "guard": guard,
        }

    def _native_rows(
        self,
        targets: list[str],
        *,
        start_priority: int = 0,
        max_depth: int = 5,
        match_type: str = "exact",
    ) -> list[dict[str, object]]:
        """Native parent-walk rows, one per target source."""
        return [
            self._strategy_row(
                phase="native",
                walk_kind="parent",
                target_source=t,
                match_type=match_type,
                priority=start_priority + i,
                max_depth=max_depth,
            )
            for i, t in enumerate(targets)
        ]

    def _snomed_fallback_rows(
        self,
        targets: list[str],
        *,
        start_priority: int,
        max_depth: int = 5,
        guard: str = "snomed_top_level",
        match_type: str = "broader",
    ) -> list[dict[str, object]]:
        """SNOMED fallback rows, one per target source."""
        return [
            self._strategy_row(
                phase="fallback",
                walk_kind="snomed",
                target_source=t,
                match_type=match_type,
                priority=start_priority + i,
                max_depth=max_depth,
                guard=guard,
            )
            for i, t in enumerate(targets)
        ]

    def _original_row(self, *, priority: int = 99) -> dict[str, object]:
        """Terminal 'original display' row."""
        return self._strategy_row(
            phase="original",
            walk_kind="none",
            target_source=None,
            match_type="original",
            priority=priority,
            max_depth=0,
        )

    def friendly_strategy_rows(self) -> list[dict[str, object]]:
        """Default: generic SNOMED fallback rows.

        Composed via the helpers so subclasses can re-use the same row
        schema without re-emitting dict literals.
        """
        return (
            self._native_rows(["MEDLINEPLUS", "CHV"]) +
            self._snomed_fallback_rows(["MEDLINEPLUS"], start_priority=2) +
            [self._original_row()]
        )

    def atom_display_rank(self) -> str:
        """Default: basic suppress-based ranking."""
        return (
            "CASE WHEN SUPPRESS = 'N' THEN 0 ELSE 1 END, "
            "CASE TTY WHEN 'PT' THEN 0 WHEN 'MH' THEN 1 WHEN 'LN' THEN 2 ELSE 3 END, "
            "AUI"
        )
