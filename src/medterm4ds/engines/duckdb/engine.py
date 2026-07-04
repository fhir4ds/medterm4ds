"""LocalDuckDBEngine — composed from per-domain mixins.

This module re-exports the engine class plus all shared helpers/constants
from ``_engine_base.py`` for backward compatibility with other duckdb
submodules (hierarchy.py, mappings.py, etc.) that import them.
"""

from __future__ import annotations

# Re-export everything from _engine_base for backward compat with
# hierarchy.py / mappings.py / patient_friendly.py / resolution.py / etc.
# that import constants and helpers from this module.
from medterm4ds.engines.duckdb._engine_base import *  # noqa: F401,F403

from medterm4ds.engines.duckdb._mixins._EngineState import _EngineState
from medterm4ds.engines.duckdb._mixins._LookupOps import _LookupOps
from medterm4ds.engines.duckdb._mixins._DiscoveryOps import _DiscoveryOps
from medterm4ds.engines.duckdb._mixins._HierarchyOps import _HierarchyOps
from medterm4ds.engines.duckdb._mixins._MappingOps import _MappingOps
from medterm4ds.engines.duckdb._mixins._ResolutionOps import _ResolutionOps
from medterm4ds.engines.duckdb._mixins._OptimizeOps import _OptimizeOps
from medterm4ds.engines.duckdb._mixins._PatientFriendlyOps import _PatientFriendlyOps
from medterm4ds.engines.duckdb._mixins._IndicationsOps import _IndicationsOps


class LocalDuckDBEngine(
    _EngineState,
    _LookupOps,
    _DiscoveryOps,
    _HierarchyOps,
    _MappingOps,
    _ResolutionOps,
    _OptimizeOps,
    _PatientFriendlyOps,
    _IndicationsOps,
):
    """Low-memory DuckDB engine for patient-friendly batch resolution.

    Composed from per-domain mixins (each in
    ``engines/duckdb/_mixins/<Domain>.py``). Mixins share state via ``self``;
    see ``_EngineState`` for the constructor + cache lifecycle.

    Mixin layout (MRO order, _EngineState first):
    ``_EngineState / _LookupOps / _DiscoveryOps / _HierarchyOps / _MappingOps /
    _ResolutionOps / _OptimizeOps / _PatientFriendlyOps / _IndicationsOps``

    Why mixins instead of a single class: the original 1500-line god class
    had 64 methods across 9 domains. Mixins keep each domain's methods in a
    focused file (~50-200 lines each) while preserving the flat
    ``engine.method(...)`` API that all callers expect.

    All shared constants and helpers live in ``_engine_base.py`` and are
    re-exported here for backward compatibility with the other duckdb
    submodules (``hierarchy.py``, ``mappings.py``, etc.) that import them
    via ``from medterm4ds.engines.duckdb.engine import _helper``.
    """

# Backward-compatible alias for pre-0.0.1 naming.
LocalLiteEngine = LocalDuckDBEngine
