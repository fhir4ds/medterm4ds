"""RxNorm TTY topology, group TTYs, ingredient rules, and shortest-path computation."""

from __future__ import annotations

from collections import deque
from typing import Any

from .base import DefaultStrategy

# ---------------------------------------------------------------------------
# TTY Topology -- exact adjacency table from RxNav
# ---------------------------------------------------------------------------

RXNORM_TTY_TOPOLOGY: dict[str, tuple[str, ...]] = {
    "BN": ("SBD", "IN"),
    "SBD": ("BN", "SCD", "SBDF", "SBDG", "SBDC", "BPCK", "SCDC"),
    "SBDC": ("SBD", "SBDF", "IN"),
    "SBDF": ("SBD", "SCDF"),
    "SCD": ("SBD", "SCDC", "SCDF", "SCDG", "GPCK", "DF", "MIN"),
    "SCDC": ("SCD", "SBD", "IN", "PIN"),
    "SCDF": ("SCD", "SBDF"),
    "BPCK": ("SBD", "GPCK"),
    "GPCK": ("SCD", "BPCK"),
    "IN": ("SCDC", "BN"),
    "MIN": ("SCD", "IN"),
    "PIN": ("IN", "SCDC"),
    "DF": ("SCD",),
    "SBDG": ("SBD", "SCDG"),
    "SCDG": ("SCD", "SBDG", "DFG"),
    "DFG": ("SCDG",),
}


# All TTYs that appear as keys or as neighbors
RXNORM_KNOWN_TTYS: frozenset[str] = frozenset(
    RXNORM_TTY_TOPOLOGY.keys()
    | {tty for neighbors in RXNORM_TTY_TOPOLOGY.values() for tty in neighbors}
)

# ---------------------------------------------------------------------------
# Group Target TTYs -- the TTYs considered "group" concepts
# ---------------------------------------------------------------------------

RXNORM_GROUP_TTYS: frozenset[str] = frozenset({
    "SCD",
    "SBD",
    "SCDF",
    "SBDF",
    "GPCK",
    "BPCK",
    "SBDG",
    "SCDG",
    "SBDC",
    "DFG",
})

# ---------------------------------------------------------------------------
# TTY priority for base atom selection
# ---------------------------------------------------------------------------

RXNORM_BASE_TTY_PRIORITY: dict[str, int] = {
    "SCDG": 0,
    "SBDG": 1,
    "SCD": 2,
    "SBD": 3,
    "SCDC": 4,
    "SBDC": 5,
    "SCDF": 6,
    "SBDF": 7,
    "GPCK": 8,
    "BPCK": 9,
    "MIN": 10,
    "IN": 11,
    "PIN": 12,
    "BN": 13,
    "DF": 14,
    "DFG": 15,
}


# ---------------------------------------------------------------------------
# Shortest path computation via BFS
# ---------------------------------------------------------------------------

def compute_tty_paths(
    topology: dict[str, tuple[str, ...]] | None = None,
) -> list[dict[str, Any]]:
    """Compute shortest paths between all TTY pairs.

    Returns a list of dicts, each with:
        start_tty, target_tty, match_type, target_order, steps (list of TTY strings)
    """
    if topology is None:
        topology = RXNORM_TTY_TOPOLOGY

    all_ttys = sorted(
        topology.keys() | {t for neighbors in topology.values() for t in neighbors}
    )

    # BFS helper to find ALL shortest paths
    def _bfs_all(start: str, target: str) -> list[list[str]]:
        if start == target:
            return [[start]]
        if start not in topology:
            return []
        queue: deque[list[str]] = deque([[start]])
        shortest_length = float("inf")
        results: list[list[str]] = []
        visited: dict[str, int] = {start: 0}
        while queue:
            path = queue.popleft()
            if len(path) > shortest_length:
                break
            for neighbor in topology.get(path[-1], ()):
                if neighbor == target:
                    shortest_length = len(path)
                    results.append([*path, neighbor])
                elif visited.get(neighbor, float("inf")) >= len(path):
                    visited[neighbor] = len(path)
                    queue.append([*path, neighbor])
        return results

    paths: list[dict[str, Any]] = []
    for start_tty in all_ttys:
        target_specs: list[tuple[str, int, str]] = []

        # Group phase: target SCDG for group TTYs
        if start_tty in RXNORM_GROUP_TTYS:
            target_specs.append(("SCDG", 0, "group"))

        # Ingredient phase
        if start_tty in {"IN", "MIN"}:
            ingredient_targets = (start_tty,)
        elif start_tty in {"PIN", "SCDC"}:
            ingredient_targets = ("IN", "MIN")
        else:
            ingredient_targets = ("MIN", "IN")

        for target_order, target_tty in enumerate(ingredient_targets, 1):
            target_specs.append((target_tty, target_order, "ingredient"))

        for target_tty, target_order, match_type in target_specs:
            found_paths = _bfs_all(start_tty, target_tty)
            for path in found_paths:
                paths.append({
                    "start_tty": start_tty,
                    "target_tty": target_tty,
                    "match_type": match_type,
                    "target_order": target_order,
                    "steps": path,
                })

    return paths


def find_tty_path(start_tty: str, target_tty: str) -> list[str]:
    """Return the shortest TTY path from *start_tty* to *target_tty*.

    Both endpoints are included in the returned list.  Returns an empty
    list when no path exists.
    """
    start_tty = start_tty.upper()
    target_tty = target_tty.upper()
    if start_tty == target_tty:
        return [start_tty]
    if start_tty not in RXNORM_TTY_TOPOLOGY:
        return []
    queue: deque[list[str]] = deque([[start_tty]])
    visited = {start_tty}
    while queue:
        path = queue.popleft()
        for next_tty in RXNORM_TTY_TOPOLOGY.get(path[-1], ()):
            if next_tty == target_tty:
                return [*path, next_tty]
            if next_tty not in visited:
                visited.add(next_tty)
                queue.append([*path, next_tty])
    return []


# ---------------------------------------------------------------------------
# RxNorm strategy
# ---------------------------------------------------------------------------

class RxNormStrategy(DefaultStrategy):
    """RxNorm source strategy -- TTY topology driven, no standard hierarchy."""

    def __init__(self) -> None:
        super().__init__(source="RXNORM")

    def hierarchy_edge_sql(self) -> str | None:
        """RxNorm isa edges (SCD/SCDG/SBD/... TTY hierarchy) via mrrel.

        Found by QC-349 (EC-15 HIGH): returning None here dropped all 238,329
        RxNorm isa/inverse_isa subsumption edges from the prepared hierarchy
        build, so every hierarchy operation silently returned empty for RxNorm
        while the CapabilityStatement advertised $subsumes. The TTY topology
        (rxnorm_tty_edges) drives patient-friendly resolution; these isa edges
        drive subsumption/hierarchy. REL is authoritative for direction.
        """
        return "r.REL IN ('RB', 'RN') AND r.RELA IN ('isa', 'inverse_isa')"

    def friendly_strategy_rows(self) -> list[dict[str, object]]:
        """RxNorm patient-friendly rows use TTY topology, not hierarchy walks."""
        rows: list[dict[str, object]] = []

        for path_info in compute_tty_paths():
            rows.append({
                "phase": "topology",
                "walk_kind": "tty_traversal",
                "target_source": "RXNORM",
                "target_tty": path_info["target_tty"],
                "match_type": path_info["match_type"],
                "priority": path_info["target_order"],
                "max_depth": len(path_info["steps"]) - 1,
                "stop_on_hit": True,
                "guard": None,
            })

        # Fallback: original display
        rows.append({
            "phase": "original",
            "walk_kind": "none",
            "target_source": None,
            "target_tty": None,
            "match_type": "original",
            "priority": 99,
            "max_depth": 0,
            "stop_on_hit": True,
            "guard": None,
        })
        return rows

    def atom_display_rank(self) -> str:
        """RxNorm uses TTY priority for atom ranking."""
        cases = " ".join(
            f"WHEN '{tty}' THEN {priority}"
            for tty, priority in RXNORM_BASE_TTY_PRIORITY.items()
        )
        return (
            f"CASE upper(TTY) {cases} ELSE 99 END, "
            "CASE WHEN SUPPRESS = 'N' THEN 0 ELSE 1 END, "
            "AUI"
        )
