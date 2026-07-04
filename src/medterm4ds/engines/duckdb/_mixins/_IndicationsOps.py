"""Optional condition→medication queries (UMLS RB/RELA edges)."""


from __future__ import annotations

from medterm4ds.engines.duckdb._engine_base import *  # noqa: F401,F403
from typing import Any
from collections.abc import Sequence
from medterm4ds.core.models import CodeRef


class _IndicationsOps:
    """Optional condition→medication queries (UMLS RB/RELA edges).

    Mixin for LocalDuckDBEngine — methods share state via ``self`` (``self.con``,
    ``self.cache_prepared``, ``self.query_chunk_size``, etc.). Not intended to be
    instantiated on its own.
    """

    def get_drugs_for_indication(
        self,
        candidates: Sequence[tuple[str, str, int]],
        *,
        relationships: Sequence[str],
        max_depth: int,
        limit: int,
        include_product_groups: bool,
    ) -> list[tuple[Any, ...]]:
        """Query UMLS may_treat/may_prevent/... relationships for condition candidates.

        Returns raw SQL rows; caller (domain layer) formats them via
        indications.format_condition_medication_row.
        """
        from medterm4ds.engines.duckdb import indications
        return indications.query_condition_medication_relationships(
            self.con,
            candidates,
            relationships=relationships,
            max_depth=max_depth,
            limit=limit,
            include_product_groups=include_product_groups,
        )



    def get_ndcs_for_rxcuis(self, rxcuis: Sequence[str]) -> dict[str, list[str]]:
        """Lookup NDC codes for RxNorm codes via mrsat."""
        from medterm4ds.engines.duckdb import indications
        return indications.query_ndcs_for_rxcuis(self.con, rxcuis)

