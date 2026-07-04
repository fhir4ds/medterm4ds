"""Same-source parent/child/ancestor/descendant traversal."""


from __future__ import annotations

from medterm4ds.engines.duckdb._engine_base import *  # noqa: F401,F403
from medterm4ds.engines.duckdb import hierarchy as _hierarchy
from collections.abc import Sequence
from medterm4ds.core.models import CodeRef, CodeRelation


class _HierarchyOps:
    """Same-source parent/child/ancestor/descendant traversal.

    Mixin for LocalDuckDBEngine — methods share state via ``self`` (``self.con``,
    ``self.cache_prepared``, ``self.query_chunk_size``, etc.). Not intended to be
    instantiated on its own.
    """

    def get_code_relations(
        self,
        codes: Sequence[CodeRef],
        *,
        direction: str,
        max_depth: int = 1,
        limit: int | None = None,
    ) -> list[CodeRelation]:
        """Return same-source hierarchy relationships for input codes."""
        if not codes:
            return []
        if max_depth < 1:
            raise ValueError("max_depth must be at least 1")

        direction = direction.strip().lower()
        if direction not in {"parents", "children", "ancestors", "descendants"}:
            raise ValueError("direction must be one of parents, children, ancestors, or descendants")
        if direction in {"parents", "children"}:
            max_depth = 1

        ordered = [CodeRef(source=code.source, code=code.code) for code in codes]
        grouped: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for ordinal, ref in enumerate(ordered):
            grouped[ref.source].append((ordinal, ref.code))

        rows: list[tuple[int, CodeRelation]] = []
        relationship = _HIERARCHY_RELATIONSHIPS[direction]
        upward = direction in {"parents", "ancestors"}
        for source, source_codes in grouped.items():
            for chunk in _chunks(source_codes, self.query_chunk_size):
                rows.extend(
                    self._get_source_code_relations(
                        source,
                        chunk,
                        relationship=relationship,
                        upward=upward,
                        max_depth=max_depth,
                        limit=limit,
                    )
                )
        return [
            relation
            for _ordinal, relation in sorted(
                rows,
                key=lambda item: (
                    item[0],
                    item[1].depth,
                    item[1].target.code,
                    item[1].target_aui or "",
                ),
            )
        ]



    def _get_source_code_relations(
        self,
        source: str,
        code_ordinals: Sequence[tuple[int, str]],
        *,
        relationship: str,
        upward: bool,
        max_depth: int,
        limit: int | None = None,
    ) -> list[tuple[int, CodeRelation]]:
        return _hierarchy.get_source_code_relations(
            self,
            source,
            code_ordinals,
            relationship=relationship,
            upward=upward,
            max_depth=max_depth,
            limit=limit,
        )



    def _get_source_code_relations_prepared(
        self,
        source: str,
        code_ordinals: Sequence[tuple[int, str]],
        *,
        relationship: str,
        upward: bool,
        max_depth: int,
    ) -> list[tuple[int, CodeRelation]]:
        return _hierarchy.get_source_code_relations_prepared(
            self,
            source,
            code_ordinals,
            relationship=relationship,
            upward=upward,
            max_depth=max_depth,
        )

