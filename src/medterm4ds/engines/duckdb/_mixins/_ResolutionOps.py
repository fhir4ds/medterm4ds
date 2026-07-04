"""Per-source resolution of historical/obsolete/NDC inputs to active codes."""


from __future__ import annotations

from medterm4ds.engines.duckdb._engine_base import *  # noqa: F401,F403
from medterm4ds.engines.duckdb import mappings as _mappings
from medterm4ds.engines.duckdb import patient_friendly as _patient_friendly
from medterm4ds.engines.duckdb import resolution as _resolution
from collections.abc import Sequence
from medterm4ds.core.models import CodeRef, CodeResolution


class _ResolutionOps:
    """Per-source resolution of historical/obsolete/NDC inputs to active codes.

    Mixin for LocalDuckDBEngine — methods share state via ``self`` (``self.con``,
    ``self.cache_prepared``, ``self.query_chunk_size``, etc.). Not intended to be
    instantiated on its own.
    """

    def resolve_codes(self, codes: Sequence[CodeRef]) -> list[CodeResolution]:
        """Resolve active, historical, obsolete, and NDC inputs."""
        return [self._resolve_code(CodeRef(source=code.source, code=code.code)) for code in codes]



    def _active_source_code_set(self, source):
        return _resolution._active_source_code_set(self, source=source)




    def _resolve_ndc(self, ref):
        return _resolution._resolve_ndc(self, ref=ref)




    def _replacement_candidates(self, historical):
        return _resolution._replacement_candidates(self, historical=historical)




    def _resolve_source(self, source: str, codes: Sequence[str], max_depth: int) -> list[_Row]:
        if not codes:
            return []
        if source == "RXNORM":
            return self._resolve_rxnorm(codes)
        if source == "LNC":
            return self._resolve_loinc(codes, max_depth)
        if source == "CPT":
            return self._resolve_cpt(codes, max_depth)
        if source == "CVX":
            return self._resolve_cvx(codes)
        return self._resolve_default(codes, source, max_depth)



    def _resolve_default(self, codes, source, max_depth, *, filter_broad=False):
        return _patient_friendly._resolve_default(self, codes=codes, source=source, max_depth=max_depth, filter_broad=filter_broad)




    def _apply_snomed_fallback(self, source, rows, max_depth):
        return _patient_friendly._apply_snomed_fallback(self, source=source, rows=rows, max_depth=max_depth)




    def _resolve_default_via_snomed(self, codes, source, max_depth):
        return _patient_friendly._resolve_default_via_snomed(self, codes=codes, source=source, max_depth=max_depth)





    def _resolve_rxnorm(self, codes):
        return _patient_friendly._resolve_rxnorm(self, codes=codes)



    def _resolve_loinc(self, codes, max_depth):
        return _patient_friendly._resolve_loinc(self, codes=codes, max_depth=max_depth)




    def _resolve_cpt(self, codes, max_depth):
        return _patient_friendly._resolve_cpt(self, codes=codes, max_depth=max_depth)




    def _map_cpt_targets(self, codes):
        return _mappings._map_cpt_targets(self, codes=codes)




    def _resolve_cvx(self, codes):
        return _patient_friendly._resolve_cvx(self, codes=codes)




    def _map_snomed_codes(self, codes):
        return _mappings._map_snomed_codes(self, codes=codes)




    def _map_snomed_broader(self, codes):
        return _mappings._map_snomed_broader(self, codes=codes)




    def _resolve_snomed(self, snomed_codes, snomed_map, non_snomed, max_depth):
        return _patient_friendly._resolve_snomed(self, snomed_codes=snomed_codes, snomed_map=snomed_map, non_snomed=non_snomed, max_depth=max_depth)


