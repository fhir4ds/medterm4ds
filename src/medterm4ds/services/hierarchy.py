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
) -> list[CodeRelation]:
    """Return descendant relationships up to max_depth."""
    return get_code_relations(codes, engine=engine, direction="descendants", max_depth=max_depth)


def normalize_hierarchy_direction(direction: str) -> HierarchyDirection:
    """Normalize hierarchy direction aliases."""
    normalized = _DIRECTION_ALIASES.get(direction.strip().lower())
    if normalized is None:
        raise ValueError(
            "direction must be one of parents, children, ancestors, or descendants"
        )
    return cast(HierarchyDirection, normalized)
