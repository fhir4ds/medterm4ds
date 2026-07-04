"""Cross-source mapping via same-CUI atoms and target-hierarchy walks."""


from __future__ import annotations

from medterm4ds.engines.duckdb._engine_base import *  # noqa: F401,F403
from medterm4ds.engines.duckdb import mappings as _mappings
from collections.abc import Sequence
from medterm4ds.core.models import CodeMapping, CodeRef


class _MappingOps:
    """Cross-source mapping via same-CUI atoms and target-hierarchy walks.

    Mixin for LocalDuckDBEngine — methods share state via ``self`` (``self.con``,
    ``self.cache_prepared``, ``self.query_chunk_size``, etc.). Not intended to be
    instantiated on its own.
    """

    def get_code_mappings(
        self,
        codes: Sequence[CodeRef],
        *,
        target_sources: Sequence[str],
        max_results_per_code: int = 50,
        max_depth: int = 0,
        include_target_ancestors: bool = False,
        include_target_descendants: bool = False,
    ) -> list[CodeMapping]:
        """Return same-CUI active target mappings for input codes."""
        if not codes:
            return []
        if not target_sources:
            raise ValueError("target_sources must not be empty")
        if max_results_per_code < 1:
            raise ValueError("max_results_per_code must be at least 1")
        if max_depth < 0:
            raise ValueError("max_depth must be non-negative")

        ordered = [CodeRef(source=code.source, code=code.code) for code in codes]
        target_sources = _dedupe(target_sources)
        if (
            not include_target_ancestors
            and not include_target_descendants
            and (
                self._table_exists("crosswalk_edges")
                or self._table_exists("same_cui_edges")
            )
            and self._table_exists("best_atoms")
            and (max_depth == 0 or self._table_exists("walk_edges"))
        ):
            from medterm4ds.services.crosswalk_prepared import get_crosswalk_mappings

            unique_ordered: list[CodeRef] = []
            seen_refs: set[tuple[str, str]] = set()
            for ref in ordered:
                key = (ref.source, ref.code)
                if key not in seen_refs:
                    seen_refs.add(key)
                    unique_ordered.append(ref)
            prepared_mappings = get_crosswalk_mappings(
                unique_ordered,
                self.con,
                target_sources=target_sources,
                max_depth=max_depth,
            )
            prepared_rows = [
                (index, mapping)
                for index, ref in enumerate(ordered)
                for mapping in prepared_mappings
                if mapping.source.source == ref.source and mapping.source.code == ref.code
            ]
            prepared_rows = self._filter_snomed_top_level_mappings(prepared_rows)
            return _cap_mappings_per_input(prepared_rows, max_results_per_code)

        grouped: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for ordinal, ref in enumerate(ordered):
            grouped[ref.source].append((ordinal, ref.code))

        rows: list[tuple[int, CodeMapping]] = []
        for source, source_codes in grouped.items():
            for chunk in _chunks(source_codes, self.query_chunk_size):
                rows.extend(
                    self._get_source_code_mappings(
                        source,
                        chunk,
                        target_sources=target_sources,
                        max_results_per_code=max_results_per_code,
                    )
                )
                if max_depth > 0:
                    rows.extend(
                        self._get_source_ancestor_mappings(
                            source,
                            chunk,
                            target_sources=target_sources,
                            max_results_per_code=max_results_per_code,
                            max_depth=max_depth,
                        )
                    )
                    if include_target_ancestors:
                        for target_source in target_sources:
                            rows.extend(
                                self._get_target_hierarchy_mappings(
                                    source,
                                    chunk,
                                    target_sources=[target_source],
                                    max_results_per_code=max_results_per_code,
                                    max_depth=max_depth,
                                    upward=True,
                                )
                            )
                    if include_target_descendants:
                        for target_source in target_sources:
                            rows.extend(
                                self._get_target_hierarchy_mappings(
                                    source,
                                    chunk,
                                    target_sources=[target_source],
                                    max_results_per_code=max_results_per_code,
                                    max_depth=max_depth,
                                    upward=False,
                                )
                            )
        rows = self._filter_snomed_top_level_mappings(rows)
        return [
            mapping
            for _ordinal, mapping in sorted(
                rows,
                key=lambda item: (
                    item[0],
                    item[1].match_depth,
                    item[1].match_type,
                    item[1].target.source,
                    item[1].target.code,
                    item[1].target_aui or "",
                ),
            )
        ]



    def _filter_snomed_top_level_mappings(self, rows):
        return _mappings._filter_snomed_top_level_mappings(self, rows=rows)




    def _related_code_map(
        self,
        source: str,
        codes: Sequence[str],
        *,
        relationship: str,
        upward: bool,
        max_depth: int,
    ) -> dict[str, set[str]]:
        if not codes:
            return {}
        output = {str(code): set() for code in codes}
        frontier = {str(code): {str(code)} for code in codes}
        seen = {str(code): {str(code)} for code in codes}
        for _depth in range(max_depth):
            frontier_codes = sorted({code for values in frontier.values() for code in values})
            if not frontier_codes:
                break
            direct = self._direct_related_code_map(
                source,
                frontier_codes,
                relationship=relationship,
                upward=upward,
            )
            next_frontier = {origin: set() for origin in frontier}
            for origin, current_codes in frontier.items():
                for current in current_codes:
                    for target in direct.get(current, set()):
                        if target in seen[origin]:
                            continue
                        seen[origin].add(target)
                        output[origin].add(target)
                        next_frontier[origin].add(target)
            frontier = {origin: values for origin, values in next_frontier.items() if values}
        return output



    def _direct_related_code_map(
        self,
        source: str,
        codes: Sequence[str],
        *,
        relationship: str,
        upward: bool,
    ) -> dict[str, set[str]]:
        if not codes:
            return {}
        if _is_isa_relationship(relationship) and self._table_exists("walk_edges"):
            output = {str(code): set() for code in codes}
            for chunk in _chunks([str(code) for code in codes], self.query_chunk_size):
                code_placeholders = ",".join(["?"] * len(chunk))
                if upward:
                    source_code_sql = "from_code"
                    target_code_sql = "to_code"
                    filter_code_sql = "from_code"
                else:
                    source_code_sql = "to_code"
                    target_code_sql = "from_code"
                    filter_code_sql = "to_code"
                rows = self.con.execute(
                    f"""
                    SELECT DISTINCT {source_code_sql} AS source_code,
                           {target_code_sql} AS target_code
                    FROM mt4ds.walk_edges
                    WHERE source = ?
                      AND direction = 'parent'
                      AND {filter_code_sql} IN ({code_placeholders})
                    """,
                    [source, *chunk],
                ).fetchall()
                for source_code, target_code in rows:
                    output.setdefault(str(source_code), set()).add(str(target_code))
            return output

        if _is_isa_relationship(relationship):
            source_join, source_target = _source_hierarchy_join_sql(
                source,
                "c.AUI",
                upward=upward,
            )
            rel_filter_sql = ""
            rel_params: list[str] = []
        else:
            rel_values = _relationship_values(relationship)
            rel_placeholders = ",".join(["?"] * len(rel_values))
            source_join = "r.AUI1 = c.AUI" if upward else "r.AUI2 = c.AUI"
            source_target = "r.AUI2" if upward else "r.AUI1"
            rel_filter_sql = f"AND (r.RELA IN ({rel_placeholders}) OR r.REL IN ({rel_placeholders}))"
            rel_params = [*rel_values, *rel_values]
        output = {str(code): set() for code in codes}
        for chunk in _chunks([str(code) for code in codes], self.query_chunk_size):
            code_placeholders = ",".join(["?"] * len(chunk))
            rows = self.con.execute(
                f"""
                SELECT DISTINCT c.CODE AS source_code, t.CODE AS target_code
                FROM mrconso c
                JOIN mrrel r ON {source_join}
                JOIN mrconso t ON t.AUI = {source_target}
                WHERE c.SAB = ?
                  AND c.SUPPRESS = 'N'
                  AND c.CODE IN ({code_placeholders})
                  AND t.SAB = ?
                  AND t.SUPPRESS = 'N'
                  {rel_filter_sql}
                """,
                [
                    source,
                    *chunk,
                    source,
                    *rel_params,
                ],
            ).fetchall()
            for source_code, target_code in rows:
                output.setdefault(str(source_code), set()).add(str(target_code))
        return output



    def _get_source_code_mappings(self, source, code_ordinals, *, target_sources, max_results_per_code):
        return _mappings._get_source_code_mappings(self, source=source, code_ordinals=code_ordinals, target_sources=target_sources, max_results_per_code=max_results_per_code)




    def _get_source_code_mappings_prepared(self, source, code_ordinals, *, target_sources, max_results_per_code):
        return _mappings._get_source_code_mappings_prepared(self, source=source, code_ordinals=code_ordinals, target_sources=target_sources, max_results_per_code=max_results_per_code)




    def _get_source_ancestor_mappings(self, source, code_ordinals, *, target_sources, max_results_per_code, max_depth):
        return _mappings._get_source_ancestor_mappings(self, source=source, code_ordinals=code_ordinals, target_sources=target_sources, max_results_per_code=max_results_per_code, max_depth=max_depth)




    def _get_source_ancestor_mappings_prepared(self, source, code_ordinals, *, target_sources, max_results_per_code, max_depth):
        return _mappings._get_source_ancestor_mappings_prepared(self, source=source, code_ordinals=code_ordinals, target_sources=target_sources, max_results_per_code=max_results_per_code, max_depth=max_depth)




    def _get_target_hierarchy_mappings(self, source, code_ordinals, *, target_sources, max_results_per_code, max_depth, upward):
        return _mappings._get_target_hierarchy_mappings(self, source=source, code_ordinals=code_ordinals, target_sources=target_sources, max_results_per_code=max_results_per_code, max_depth=max_depth, upward=upward)




    def _get_target_hierarchy_mappings_prepared(self, source, code_ordinals, *, target_sources, max_results_per_code, max_depth, upward):
        return _mappings._get_target_hierarchy_mappings_prepared(self, source=source, code_ordinals=code_ordinals, target_sources=target_sources, max_results_per_code=max_results_per_code, max_depth=max_depth, upward=upward)


