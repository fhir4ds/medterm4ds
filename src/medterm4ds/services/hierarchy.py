"""Hierarchy traversal services."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from medterm4ds.core.models import CodeRef, CodeRelation
from medterm4ds.engines.base import HierarchyDirection, HierarchyEngine

_DIRECTION_ALIASES = {
    "parent": "parents",
    "parents": "parents",
    "child": "children",
    "children": "children",
    "ancestor": "ancestors",
    "ancestors": "ancestors",
    "descendant": "descendants",
    "descendants": "descendants",
}


def get_code_relations(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: HierarchyEngine,
    *,
    direction: str,
    max_depth: int = 1,
    limit: int | None = None,
) -> list[CodeRelation]:
    """Return hierarchy relationships for one or many codes."""
    normalized_direction = normalize_hierarchy_direction(direction)
    if max_depth < 1:
        raise ValueError("max_depth must be at least 1")
    normalized = [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]
    return engine.get_code_relations(
        normalized,
        direction=normalized_direction,
        max_depth=max_depth,
        limit=limit,
    )


def get_parents(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: HierarchyEngine,
) -> list[CodeRelation]:
    """Return direct parent relationships."""
    return get_code_relations(codes, engine=engine, direction="parents", max_depth=1)


def get_children(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: HierarchyEngine,
) -> list[CodeRelation]:
    """Return direct child relationships."""
    return get_code_relations(codes, engine=engine, direction="children", max_depth=1)


def get_ancestors(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: HierarchyEngine,
    *,
    max_depth: int = 5,
) -> list[CodeRelation]:
    """Return ancestor relationships up to max_depth."""
    return get_code_relations(codes, engine=engine, direction="ancestors", max_depth=max_depth)


def get_descendants(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: HierarchyEngine,
    *,
    max_depth: int = 5,
    limit: int | None = None,
) -> list[CodeRelation]:
    """Return descendant relationships up to max_depth."""
    return get_code_relations(codes, engine=engine, direction="descendants", max_depth=max_depth, limit=limit)


def get_descendants_bfs(
    seed: CodeRef,
    engine: HierarchyEngine,
    *,
    max_depth: int = 5,
    limit: int | None = None,
    stop_at: str | None = None,
) -> tuple[list[CodeRelation], bool]:
    """Layer-by-layer BFS over descendants using direct children queries.

    Each layer is one batched SQL query (children of all frontier codes via
    get_children, which is depth=1). Total queries = max_depth. Each node is
    visited exactly once via a visited set, so cost is O(nodes) not O(paths).

    This bypasses the recursive CTE in get_descendants, which uses path-string
    cycle prevention that enumerates every distinct path through the subtree
    and explodes for wide SNOMED subtrees (Diabetes Mellitus timed out at
    5+ minutes via the CTE; BFS does it in <1s).

    Args:
        seed: root code to walk from.
        engine: HierarchyEngine (typically LocalDuckDBEngine).
        max_depth: max levels to descend. Default 5.
        limit: optional cap on number of descendant relations returned. The
            walk stops as soon as the cap is hit (early-exit), so callers
            asking for 20 results don't pay for the full subtree.
        stop_at: optional target code. If set, the walk returns as soon as
            `stop_at` is reached (early-exit), with the matching relation
            included in results. Used by $subsumes to check "is B a descendant
            of A" without walking the entire A subtree.

    Returns (relations, depth_cap_hit) where depth_cap_hit is True if the BFS
    reached max_depth with frontier still non-empty AND stop_at (if set) was
    not found (i.e. there were more descendants beyond the cap).
    """
    if max_depth < 1:
        return [], False
    visited: set[str] = {seed.code}
    frontier: list[str] = [seed.code]
    results: list[CodeRelation] = []
    depth_cap_hit = False
    found_target = False
    for _depth in range(max_depth):
        if not frontier:
            break
        if limit is not None and len(results) >= limit:
            break
        refs = [CodeRef(source=seed.source, code=c) for c in frontier]
        children = get_children(refs, engine=engine)
        new_frontier: list[str] = []
        seen_this_layer: set[str] = set()
        for rel in children:
            child_code = rel.target.code
            if child_code in visited or child_code in seen_this_layer:
                continue
            seen_this_layer.add(child_code)
            visited.add(child_code)
            results.append(rel)
            new_frontier.append(child_code)
            if stop_at is not None and child_code == stop_at:
                found_target = True
                break
            if limit is not None and len(results) >= limit:
                break
        if found_target:
            break
        frontier = new_frontier
    else:
        # Loop completed without break = depth cap was the limiter
        if frontier:
            depth_cap_hit = True
    return results, depth_cap_hit


def is_descendant(
    ancestor: CodeRef,
    candidate: CodeRef,
    engine: HierarchyEngine,
    *,
    max_depth: int = 20,
) -> bool:
    """Return True if `candidate` is a descendant of `ancestor` (within max_depth).

    Uses get_descendants_bfs with stop_at=candidate.code for early exit, so
    the typical case (one hop) is one SQL query. The previous implementation
    via get_descendants(max_depth=20) walked the entire subtree via a slow
    recursive CTE and timed out for wide SNOMED roots.
    """
    if ancestor.code == candidate.code:
        return False
    if ancestor.source != candidate.source:
        return False
    relations, _ = get_descendants_bfs(
        ancestor,
        engine=engine,
        max_depth=max_depth,
        stop_at=candidate.code,
    )
    return any(r.target.code == candidate.code for r in relations)


def normalize_hierarchy_direction(direction: str) -> HierarchyDirection:
    """Normalize hierarchy direction aliases."""
    normalized = _DIRECTION_ALIASES.get(direction.strip().lower())
    if normalized is None:
        raise ValueError(
            "direction must be one of parents, children, ancestors, or descendants"
        )
    return cast(HierarchyDirection, normalized)
